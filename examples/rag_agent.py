"""
Ludex Use Case #3: RAG Agent

Retrieval-Augmented Generation agent.
Load documents into MemoryBlock, then ask questions.
The agent searches memories for relevant context before answering.

Demonstrates: Provider + Engine + Memory + Resilience + Emotion + Immune

Run:
    python examples/rag_agent.py
    python examples/rag_agent.py --model gemma3:4b --docs path/to/docs/
    python examples/rag_agent.py --model llama3.1:8b --verbose
"""

import sys
import os
import argparse
import glob

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.organism import Organism
from ludex.blocks.provider import ProviderBlock
from ludex.blocks.engine import EngineBlock
from ludex.blocks.resilience import ResilienceBlock
from ludex.blocks.tracking import TrackingBlock
from ludex.blocks.hooks import HooksBlock
from ludex.blocks.memory import MemoryBlock
from ludex.blocks.immune import ImmuneBlock
from ludex.blocks.emotion import EmotionBlock


RAG_SYSTEM_PROMPT = """You are a knowledgeable assistant with access to a document memory.
When answering questions, use the recalled context provided to you.
If the context contains relevant information, cite it.
If the context doesn't cover the question, say so honestly.
Be concise and accurate."""


def build_rag_agent(model: str = "llama3.1:8b", provider: str = "ollama",
                    memory_dir: str = "") -> Organism:
    """Assemble a RAG organism."""
    return Organism(
        blocks=[
            ProviderBlock(provider=provider, model=model),
            EngineBlock(
                max_turns=200,
                token_budget=100000,
                system_prompt=RAG_SYSTEM_PROMPT,
            ),
            ResilienceBlock(max_retries=2, initial_delay_ms=1000, max_delay_ms=10000,
                            circuit_breaker_threshold=10),
            TrackingBlock(experiment_name="rag"),
            HooksBlock(),
            MemoryBlock(storage_dir=memory_dir or "./ludex_rag_memory", auto_capture=False),
            ImmuneBlock(sensitivity=1.0),
            EmotionBlock(method="behavioral"),
        ],
        name="rag-agent",
        config={"model": model},
    )


def ingest_text(agent: Organism, text: str, source: str = "", chunk_size: int = 500):
    """Split text into chunks and store in MemoryBlock."""
    memory = agent.get_block("memory")
    words = text.split()
    chunks = []
    current = []
    current_len = 0

    for word in words:
        current.append(word)
        current_len += len(word) + 1
        if current_len >= chunk_size:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
    if current:
        chunks.append(" ".join(current))

    for i, chunk in enumerate(chunks):
        memory.handle_remember(
            content=chunk,
            memory_type="semantic",
            tags=["document", source] if source else ["document"],
            importance=0.7,
            source=source or "ingested",
            metadata={"chunk_index": i, "total_chunks": len(chunks)},
        )

    return len(chunks)


def ingest_file(agent: Organism, filepath: str) -> int:
    """Load a text file into memory."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
        filename = os.path.basename(filepath)
        chunks = ingest_text(agent, text, source=filename)
        print(f"  Ingested: {filename} ({chunks} chunks)")
        return chunks
    except Exception as e:
        print(f"  Error ingesting {filepath}: {e}")
        return 0


def ingest_directory(agent: Organism, dirpath: str, extensions: list[str] = None) -> int:
    """Load all text files from a directory."""
    exts = extensions or [".txt", ".md", ".py", ".json", ".csv"]
    total = 0
    for ext in exts:
        for filepath in glob.glob(os.path.join(dirpath, f"**/*{ext}"), recursive=True):
            total += ingest_file(agent, filepath)
    return total


def ask(agent: Organism, question: str, verbose: bool = False) -> str:
    """Ask a question with RAG retrieval."""
    memory = agent.get_block("memory")
    engine = agent.get_block("engine")
    emotion = agent.get_block("emotion")

    # Retrieve relevant memories
    results = memory.handle_recall(question, memory_type="semantic", limit=3)

    # Build context from retrieved memories
    context = ""
    if results:
        context_parts = []
        for i, r in enumerate(results):
            source = r.memory.source or "unknown"
            context_parts.append(f"[Source: {source}, relevance: {r.relevance:.2f}]\n{r.memory.content}")
        context = "\n\n---\n\n".join(context_parts)

    # Build prompt with context
    if context:
        prompt = f"Context from documents:\n{context}\n\nQuestion: {question}"
    else:
        prompt = f"No relevant documents found. Question: {question}"

    if verbose:
        print(f"  Retrieved {len(results)} memories")
        for r in results:
            print(f"    [{r.relevance:.2f}] {r.memory.content[:60]}...")

    # Get response
    result = engine.handle_submit(prompt)

    # Analyze emotion
    if emotion and result.response:
        emotion.handle_analyze_emotion(text=result.response)

    return result.response


def main():
    parser = argparse.ArgumentParser(description="Ludex RAG Agent")
    parser.add_argument("--model", default="llama3.1:8b")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--docs", default="", help="Directory of documents to ingest")
    parser.add_argument("--file", default="", help="Single file to ingest")
    parser.add_argument("--memory-dir", default="./ludex_rag_memory")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    print("=" * 50)
    print("  LUDEX RAG AGENT")
    print(f"  Model: {args.model}")
    print("  Organs: Provider, Engine, Memory, Resilience,")
    print("          Immune, Emotion")
    print("=" * 50)

    agent = build_rag_agent(args.model, args.provider, args.memory_dir)
    memory = agent.get_block("memory")

    # Ingest documents
    if args.docs:
        print(f"\nIngesting documents from: {args.docs}")
        total = ingest_directory(agent, args.docs)
        print(f"  Total: {total} chunks ingested")
    elif args.file:
        print(f"\nIngesting file: {args.file}")
        ingest_file(agent, args.file)

    # Show memory stats
    stats = memory.stats()
    print(f"\nMemory: {stats['total']} entries ({stats['semantic']} semantic, {stats['episodic']} episodic)")

    # If no documents loaded, offer to ingest inline
    if stats["total"] == 0:
        print("\nNo documents loaded. You can paste text inline.")
        print("Type 'ingest:' followed by text to add to memory.")
        print("Type 'load:path' to load a file.")

    print("\nType your question (or 'quit' to exit, 'stats' for memory info)")
    print("-" * 50)

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() == "quit":
            break
        if user_input.lower() == "stats":
            s = memory.stats()
            print(f"  Memory: {s}")
            emo = agent.get_block("emotion")
            if emo:
                print(f"  Emotion: {emo.handle_get_emotional_state()['current']}")
            continue

        # Inline ingest
        if user_input.lower().startswith("ingest:"):
            text = user_input[7:].strip()
            chunks = ingest_text(agent, text, source="inline")
            print(f"  Ingested {chunks} chunks")
            continue

        if user_input.lower().startswith("load:"):
            filepath = user_input[5:].strip()
            ingest_file(agent, filepath)
            continue

        # Ask question with RAG
        response = ask(agent, user_input, verbose=args.verbose)
        print(f"\n{response}")


if __name__ == "__main__":
    main()
