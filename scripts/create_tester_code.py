"""테스터용 워크스페이스와 테스터 코드를 생성하는 CLI 스크립트.

사용법:
    uv run python -m scripts.create_tester_code
    uv run python -m scripts.create_tester_code --code TESTER-2026-DEMO
"""

import argparse
import secrets

from app.core.database import SessionLocal
from app.domains.account.models import TesterCode, Workspace
from app.domains.account.service import hash_token


def generate_code() -> str:
    part1 = secrets.token_hex(2).upper()
    part2 = secrets.token_hex(2).upper()
    return f"TESTER-{part1}-{part2}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Create workspace and tester code")
    parser.add_argument(
        "--code", type=str, default=None, help="직접 지정할 테스터 코드 (TESTER-...)"
    )
    args = parser.parse_args()

    raw_code = args.code.strip() if args.code else generate_code()
    if not raw_code.startswith("TESTER-"):
        raise ValueError("테스터 코드는 'TESTER-'로 시작해야 합니다.")

    with SessionLocal() as db:
        workspace = Workspace(owner_type="TESTER_CODE")
        db.add(workspace)
        db.flush()

        tester_code = TesterCode(
            code_hash=hash_token(raw_code),
            workspace_id=workspace.id,
            status="ACTIVE",
        )
        db.add(tester_code)
        db.commit()

        print(f"생성 완료 -> workspace_id={workspace.id}, tester_code={raw_code}")


if __name__ == "__main__":
    main()
