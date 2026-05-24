from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import uvicorn

from legal_agent.api.app import create_app
from legal_agent.core.config import load_settings
from legal_agent.db.migrate import migrate
from legal_agent.evaluation.offline import OfflineEvalOptions, run_offline_evaluation
from legal_agent.rag.ingest import backfill_missing_embeddings
from legal_agent.rag.ingest import bootstrap_labor_dispute
from legal_agent.workflows.client import run_embedding_backfill_workflow
from legal_agent.workflows.worker import run_worker


def main() -> None:
    parser = argparse.ArgumentParser(prog="legal-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("serve-api")
    sub.add_parser("worker")
    sub.add_parser("rag-worker")
    sub.add_parser("embedding-worker")
    embedding = sub.add_parser("embedding-backfill")
    embedding.add_argument("--limit", type=int, default=100)
    embedding.add_argument("--local", action="store_true", help="Run in-process instead of scheduling Temporal workflow.")
    rag = sub.add_parser("rag-ingest")
    rag.add_argument("action", choices=["bootstrap"])
    rag.add_argument("--domain", default="labor_dispute")
    eval_parser = sub.add_parser("eval-offline")
    eval_parser.add_argument("--dataset", default="evaluations/labor_dispute/offline_minimal.json")
    eval_parser.add_argument("--out", default="")
    eval_parser.add_argument("--no-fail-on-gate", action="store_true")
    args = parser.parse_args()
    settings = load_settings()

    if args.command == "migrate":
        migrate(settings)
        print(json.dumps({"status": "ok"}, ensure_ascii=False))
    elif args.command == "serve-api":
        uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)
    elif args.command == "worker":
        asyncio.run(run_worker(settings))
    elif args.command == "rag-worker":
        asyncio.run(run_worker(settings, kind="rag"))
    elif args.command == "embedding-worker":
        asyncio.run(run_worker(settings, kind="embedding"))
    elif args.command == "embedding-backfill":
        if args.local or not settings.temporal_start_workflows:
            result = backfill_missing_embeddings(settings, limit=args.limit)
        else:
            result = asyncio.run(run_embedding_backfill_workflow(settings, limit=args.limit))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "rag-ingest":
        if args.domain != "labor_dispute":
            raise SystemExit("only labor_dispute is supported in this demo")
        result = bootstrap_labor_dispute(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "eval-offline":
        result = run_offline_evaluation(
            settings,
            OfflineEvalOptions(
                dataset_path=Path(args.dataset),
                output_path=Path(args.out) if args.out else None,
                fail_on_gate=not args.no_fail_on_gate,
            ),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
