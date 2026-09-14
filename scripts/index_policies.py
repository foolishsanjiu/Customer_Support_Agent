import argparse
import asyncio

from app.core.config import get_settings
from app.policy.embeddings import BGEEmbeddingClient
from app.policy.retriever import ChromaPolicyRetriever


async def run(query: str | None, policy_type: str | None) -> None:
    settings = get_settings()
    retriever = ChromaPolicyRetriever(
        path=settings.chroma_path,
        policy_directory=settings.policy_directory,
        embeddings=BGEEmbeddingClient(
            settings.embedding_model,
            settings.embedding_cache_dir,
        ),
        top_k=settings.policy_top_k,
    )
    await retriever.ensure_indexed()
    print(f"indexed_policy_chunks={retriever.collection.count()}")
    if query:
        matches = await retriever.search(query, policy_type=policy_type)
        for match in matches:
            print(f"{match.policy_id}\t{match.section}\t{match.distance:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and smoke-test the policy index")
    parser.add_argument("--query")
    parser.add_argument("--policy-type")
    args = parser.parse_args()
    asyncio.run(run(args.query, args.policy_type))


if __name__ == "__main__":
    main()
