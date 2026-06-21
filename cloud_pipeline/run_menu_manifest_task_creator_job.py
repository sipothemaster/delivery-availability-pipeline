import os
import sys

from cloud_pipeline import create_menu_manifest_tasks


def add_arg(args, name, value=None):
    args.append(name)
    if value is not None:
        args.append(str(value))


def main():
    args = []
    add_arg(args, "--run-id", os.environ["MENU_MANIFEST_RUN_ID"])
    add_arg(args, "--limit", os.getenv("MENU_MANIFEST_LIMIT", "1000"))
    add_arg(args, "--seed", os.getenv("MENU_MANIFEST_SEED", os.environ["MENU_MANIFEST_RUN_ID"]))
    add_arg(args, "--queue-id", os.environ["MENU_MANIFEST_QUEUE_ID"])
    add_arg(args, "--service-url", os.environ["MENU_MANIFEST_WORKER_SERVICE_URL"])
    add_arg(
        args,
        "--oidc-service-account-email",
        os.environ["MENU_MANIFEST_OIDC_SERVICE_ACCOUNT_EMAIL"],
    )
    add_arg(
        args,
        "--max-dispatches-per-second",
        os.getenv("MENU_MANIFEST_MAX_DISPATCHES_PER_SECOND", "1"),
    )
    add_arg(
        args,
        "--max-concurrent-dispatches",
        os.getenv("MENU_MANIFEST_MAX_CONCURRENT_DISPATCHES", "4"),
    )
    add_arg(args, "--create-task-workers", os.getenv("MENU_MANIFEST_CREATE_TASK_WORKERS", "32"))
    if os.getenv("MENU_MANIFEST_SKIP_QUEUE_CREATE", "true").lower() in {"1", "true", "yes"}:
        add_arg(args, "--skip-queue-create")

    sys.argv = ["create_menu_manifest_tasks", *args]
    create_menu_manifest_tasks.main()


if __name__ == "__main__":
    main()
