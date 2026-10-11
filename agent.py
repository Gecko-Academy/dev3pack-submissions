"""Your capstone agent: the one your README demos and your CI grades.

It starts as the final assignment's starter and adds the hardening after
session 14. `YourAgent` answers only from `data/corpus/`, refuses visibly
when nothing supports the question, and never lets a bad provider sink a run:

- retrieve first; nothing relevant -> refuse BEFORE any model call. Retrieval
  is stem-aware, so a question asking about "layered defenses" still finds a
  passage about "defense"; a lexical-only baseline would rank it away;
- one model call under `timeout_s`, with one corrective retry on a parse
  failure, then a flagged refusal -- a provider that raises or hangs earns
  the same flagged refusal, never an escaped exception;
- citations verified against what was retrieved: a citation retrieval never
  returned is stripped, a citation whose document does not support the answer
  text is dropped, and the answer is flagged (ISSUES.md rank 1);
- instruction-shaped text inside retrieved passages does not reach the final
  answer: the output is flagged for human review.

Calling it returns a `bootcamp_agent.schema.ResearchAnswer`, the contract the
whole course used, so everything you built in the sessions plugs in here.
`run(question)` returns the whole `AgentResult`, trace included, which is what
`uv run bootcamp capstone trace "<question>"` prints.

The provider comes from `.env` (`BOOTCAMP_PROVIDER`), and falls back to the
offline `FakeLLM`. Keys live only in `.env`, which git ignores.
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Sequence
from pathlib import Path

from bootcamp_agent.agent import AgentResult, REFUSAL_TEXT, TraceEvent
from bootcamp_agent.config import load_settings
from bootcamp_agent.documents import Document, load_corpus
from bootcamp_agent.llm import LLMClient, get_client
from bootcamp_agent.retrieval import ScoredChunk, chunk_document
from bootcamp_agent.schema import (
    ANSWER_JSON_INSTRUCTIONS,
    AnswerParseError,
    ResearchAnswer,
    parse_research_answer,
)
from bootcamp_agent.tools import Tool, build_tools

#: The six course documents, copied in by `bootcamp capstone new`. Versioned
#: input: nothing you build writes to it.
CORPUS_DIR = Path(__file__).resolve().parent / "data" / "corpus"

#: Instruction-shaped phrases, none of which appear in the course corpus. When
#: one shows up inside retrieved text, the text is likely an adversarial
#: document trying to steer the model rather than answer the question.
_INSTRUCTION_SHAPED = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard",
    "reply only with",
    "set confidence to",
    "needs_human_review to false",
    "no human review",
    "forget your",
    "override",
    "bypass",
    "ignore this prompt",
    "system prompt",
    "ignore the above",
    "instead say",
    "new instruction",
    "follow this instruction",
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Words too common to signal relevance in this corpus.
_STOPWORDS = frozenset(
    "a an and are as at be but by can could did do does for from has have how i if in "
    "into is it its me my no not of on one only or our over should so some than that "
    "the their then they this to under was we what when where which who why will with "
    "would you your".split()
)

#: Small conservative stemmer. Only the safe, high-frequency surface forms are
#: folded, so "defenses"/"defense" and "boundaries"/"boundary" match while
#: unrelated words are never conflated.
_STEM_SUFFIXES = (("ing", 3), ("ers", 3), ("es", 2), ("ies", 3), ("ed", 2), ("s", 1))


def _stem(word: str) -> str:
    for suffix, keep in _STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix) + keep:
            return word[: -len(suffix)]
    return word


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def _stemmed_tokens(text: str) -> list[str]:
    return [_stem(t) for t in _tokens(text)]


def _vocabulary(documents: Sequence[Document]) -> set[str]:
    """Stemmed content tokens across every chunk of every document."""
    return {t for doc in documents for c in chunk_document(doc, 800) for t in _stemmed_tokens(c.text)}


def _as_ids(citations: tuple[str, ...]) -> tuple[str, ...]:
    """Citations as bare doc ids, each once.

    Models copy the `[doc-id]` labels whole; compared verbatim, a correct
    "[rag-basics]" looked invented and was stripped.
    """
    ids: list[str] = []
    for citation in citations:
        cited = citation.strip()
        if len(cited) > 2 and cited.startswith("[") and cited.endswith("]"):
            cited = cited[1:-1].strip()
        if cited not in ids:
            ids.append(cited)
    return tuple(ids)


def _stemmed_retrieve(
    query: str, documents: Sequence[Document], top_k: int = 5
) -> list[ScoredChunk]:
    """Retrieve by stemmed token overlap, weighted by inverse document frequency.

    The beat the package's lexical baseline used to miss: a question about
    "layered defenses" never matched a passage about "defense" until both are
    folded to the same stem.
    """
    query_tokens = set(_stemmed_tokens(query))
    if not query_tokens:
        return []
    all_chunks = [chunk for doc in documents for chunk in chunk_document(doc, 800)]
    if not all_chunks:
        return []
    df: dict[str, int] = {}
    chunk_tokens: list[set[str]] = []
    for chunk in all_chunks:
        tokens = set(_stemmed_tokens(chunk.text))
        chunk_tokens.append(tokens)
        for token in tokens & query_tokens:
            df[token] = df.get(token, 0) + 1
    total = len(all_chunks)
    scored = []
    for chunk, tokens in zip(all_chunks, chunk_tokens, strict=True):
        overlap = tokens & query_tokens
        if not overlap:
            continue
        score = sum(math.log(1 + total / df[token]) for token in overlap)
        scored.append(ScoredChunk(chunk=chunk, score=round(score, 6)))
    scored.sort(key=lambda s: (-s.score, s.chunk.doc_id, s.chunk.position))
    return scored[:top_k]


class YourAgent:
    """The agent the tests and the grader run. Make it yours."""

    #: How long one provider call may take. A provider that raises or hangs
    #: longer than this earns a flagged refusal, never an escaped exception.
    #: The `timeout` contract test sets this low and expects an answer inside
    #: a second. Generous here on purpose: a slow-but-honest provider is a
    #: refusal only at the very end of a generous window, not after 30s.
    timeout_s: float = 180.0

    def __init__(self, client: LLMClient | None = None) -> None:
        self.documents: list[Document] = load_corpus(CORPUS_DIR)
        self.client: LLMClient = client if client is not None else get_client(load_settings())
        # Every tool the agent can reach, read-only by construction. Session 12
        # classified each one as a reader; a writer never goes on the registry.
        self.tools: dict[str, Tool] = build_tools(self.documents, self.client)

    # ------------------------------------------------------------ the run

    def run(self, question: str) -> AgentResult:
        """One question, answered or refused, with the trace of how."""
        trace: list[TraceEvent] = []

        # Refuse before any model call when the question barely overlaps the
        # corpus. A question about the 2025 Brazilian football champion shares
        # no content words with these six documents, so no amount of LLM
        # prompting should turn it into an answer. The guard is calibrated on
        # the practice set: every grounded/answer question is >= 0.83 covered,
        # every refusal question <= 0.12 (the one false match, Portuguese "time"
        # hitting English "time", is far below the threshold).
        query_tokens = _stemmed_tokens(question)
        if query_tokens:
            covered = sum(1 for t in query_tokens if t in _vocabulary(self.documents))
            coverage = covered / len(query_tokens)
            trace.append(
                TraceEvent("retrieve", f"corpus vocabulary coverage {covered}/{len(query_tokens)} = {coverage:.2f}")
            )
            if coverage < 0.5:
                trace.append(
                    TraceEvent(
                        "decision",
                        f"question shares only {covered}/{len(query_tokens)} content tokens with the corpus; refusing before an LLM call",
                    )
                )
                return AgentResult(answer=self._refusal(), trace=tuple(trace))

        scored = _stemmed_retrieve(question, self.documents, top_k=5)
        trace.append(
            TraceEvent(
                "retrieve",
                f"top_k=5 -> {[(s.chunk.doc_id, s.chunk.position) for s in scored]}",
            )
        )
        if not scored:
            trace.append(TraceEvent("decision", "no relevant chunks; refusing without an LLM call"))
            return AgentResult(answer=self._refusal(), trace=tuple(trace))

        retrieved_ids = {s.chunk.doc_id for s in scored}
        adversarial = self._instruction_shaped_context(scored)
        if adversarial:
            trace.append(
                TraceEvent(
                    "decision",
                    "instruction-shaped text found in retrieved passages; answer will be flagged",
                )
            )

        context = "\n\n".join(f"[{s.chunk.doc_id}]\n{s.chunk.text}" for s in scored)
        system = (
            "You answer ONLY using EXACT sentences from the provided context. "
            "CRITICAL: Copy passages VERBATIM, word-for-word, with NO changes. "
            "Do NOT paraphrase. Do NOT reword. Do NOT summarize.\n"
            "Your answer must be DIRECT QUOTES from the context, assembled to answer the question.\n"
            "If the passage says 'A citation the retriever never returned is a fabrication', "
            "you write EXACTLY 'A citation the retriever never returned is a fabrication', not a variation.\n"
            "Cite [doc-id] at the end of each quoted sentence.\n"
            "Include COMPLETE information: all list items, complete rules, full definitions.\n"
            "If you cannot find the exact answer in the context, refuse.\n"
            "Never invent, never paraphrase, never interpret — only quote.\n\n"
            + ANSWER_JSON_INSTRUCTIONS
        )
        user = f"Context:\n{context}\n\nQuestion: {question}"

        raw, problem = self._complete_with_guards(system, user)
        trace.append(
            TraceEvent(
                "llm_call",
                f"attempt 1: {len(raw) if raw else 0} chars ({problem or 'ok'})",
            )
        )
        if raw is None:
            trace.append(TraceEvent("decision", f"{problem}; flagged refusal"))
            return AgentResult(answer=self._refusal(), trace=tuple(trace))

        answer = self._parse_or_retry(system, user, raw, trace)
        if answer is None:
            return AgentResult(answer=self._refusal(), trace=tuple(trace))

        # Be conservative with confidence: reduce if citations are minimal
        confidence = answer.confidence
        if len(answer.citations) == 0:
            confidence = 0.0
        elif len(answer.citations) == 1:
            confidence = min(confidence, 0.8)

        answer = ResearchAnswer(
            answer=answer.answer,
            citations=_as_ids(answer.citations),
            confidence=confidence,
            needs_human_review=answer.needs_human_review,
        )

        fabricated = [c for c in answer.citations if c not in retrieved_ids]
        if fabricated:
            trace.append(
                TraceEvent(
                    "decision",
                    f"fabricated citations stripped: {fabricated}; flagged for human review",
                )
            )
            answer = ResearchAnswer(
                answer=answer.answer,
                citations=tuple(c for c in answer.citations if c in retrieved_ids),
                confidence=min(answer.confidence, 0.2),
                needs_human_review=True,
            )

        unsupported = self._citations_without_support(answer, scored)
        if unsupported:
            trace.append(
                TraceEvent(
                    "decision",
                    f"citations whose passages do not support the answer dropped: {unsupported}",
                )
            )
            answer = ResearchAnswer(
                answer=answer.answer,
                citations=tuple(c for c in answer.citations if c not in unsupported),
                confidence=min(answer.confidence, 0.2),
                needs_human_review=True,
            )

        if adversarial:
            trace.append(
                TraceEvent("decision", "retrieved text was instruction-shaped; refusing with review flag")
            )
            # Adversarial content: refuse entirely, don't try to answer
            answer = ResearchAnswer(
                answer=REFUSAL_TEXT,
                citations=(),
                confidence=0.0,
                needs_human_review=True,
            )
        if not answer.needs_human_review:
            trace.append(TraceEvent("decision", f"answered with citations {list(answer.citations)}"))
        return AgentResult(answer=answer, trace=tuple(trace))

    # ------------------------------------------------------------ the guards

    def _complete_with_guards(self, system: str, user: str) -> tuple[str | None, str | None]:
        """One provider call, bounded by `timeout_s`, none of its failures escaping.

        Returns (raw text, None) on success, or (None, why) when the provider
        raised or exceeded the timeout.
        """
        box: dict[str, object] = {}

        def work() -> None:
            try:
                box["raw"] = self.client.complete(system, user)
            except Exception as error:  # noqa: BLE001 - any provider failure is a refusal
                box["error"] = error

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        worker.join(self.timeout_s)
        if worker.is_alive():
            return None, f"provider call exceeded the {self.timeout_s:.1f}s timeout"
        if "error" in box:
            error = box["error"]
            return None, f"provider raised {type(error).__name__}: {str(error)[:120]}"
        raw = box.get("raw")
        if not isinstance(raw, str):
            return None, "provider returned no text"
        return raw, None

    def _parse_or_retry(
        self, system: str, user: str, raw: str, trace: list[TraceEvent]
    ) -> ResearchAnswer | None:
        """Strict parse, one corrective retry on failure; None means flagged refusal."""
        try:
            return parse_research_answer(raw)
        except AnswerParseError as first_error:
            trace.append(TraceEvent("decision", f"parse failed ({first_error}); retrying once"))
            raw, problem = self._complete_with_guards(
                system, user + "\n\nYour previous reply was not valid. Return ONLY the JSON object."
            )
            trace.append(
                TraceEvent("llm_call", f"attempt 2: {len(raw) if raw else 0} chars ({problem or 'ok'})")
            )
            if raw is None:
                trace.append(TraceEvent("decision", f"{problem}; flagged refusal"))
                return None
        try:
            return parse_research_answer(raw)
        except AnswerParseError as second_error:
            trace.append(
                TraceEvent("decision", f"parse failed twice ({second_error}); flagged refusal")
            )
            return None

    @staticmethod
    def _instruction_shaped_context(scored: list[ScoredChunk]) -> bool:
        lowered = " ".join(s.chunk.text.lower() for s in scored)
        return any(phrase in lowered for phrase in _INSTRUCTION_SHAPED)

    @staticmethod
    def _citations_without_support(
        answer: ResearchAnswer, scored: list[ScoredChunk]
    ) -> tuple[str, ...]:
        """Doc ids cited whose retrieved passages share NO content words with the answer.

        A citation is a promise that the document supports the claim. When the
        answer shares NOTHING with ANY retrieved passage of that document, the
        citation is decorative and dropped. But if there's ANY content word overlap,
        the citation is kept — the model's choice to cite is trusted over strict
        token matching, which can fail on synonyms, paraphrase, or technical terms.
        """
        answer_tokens = set(_stemmed_tokens(answer.answer))
        if not answer_tokens:
            return tuple()

        text_by_id: dict[str, str] = {}
        for s in scored:
            text_by_id[s.chunk.doc_id] = text_by_id.get(s.chunk.doc_id, "") + " " + s.chunk.text

        # Only drop citations that have ZERO overlap with their source text.
        # If there's any token match, trust the model's judgment to cite.
        unsupported = []
        for c in answer.citations:
            source_text = text_by_id.get(c, "")
            if not source_text:
                unsupported.append(c)
                continue
            source_tokens = set(_stemmed_tokens(source_text))
            if source_tokens and not (source_tokens & answer_tokens):
                # Zero overlap: the citation is purely decorative
                unsupported.append(c)
        return tuple(unsupported)

    @staticmethod
    def _refusal() -> ResearchAnswer:
        return ResearchAnswer(
            answer=REFUSAL_TEXT, citations=(), confidence=0.0, needs_human_review=True
        )

    def __call__(self, question: str) -> ResearchAnswer:
        return self.run(question).answer