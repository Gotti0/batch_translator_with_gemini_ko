"""테스트 전역 설정.

이벤트 루프 오염을 막는 것이 주된 목적이다. `asyncio.run()`을 쓰는 테스트는 끝나면서
루프를 닫고 현재 루프를 비워 둔다. 그 뒤에 실행되는 테스트가 `asyncio.get_event_loop()`를
부르면 "There is no current event loop in thread 'MainThread'"로 죽는데, 이 실패는 원인이
된 테스트가 아니라 뒤에 오는 테스트에서 나므로 오진하기 쉽다. 실제로 이 저장소에서는
GUI 테스트가 단독으로는 통과하고 다른 파일과 함께 돌리면 실패해, 한동안 Qt 인스턴스
공유 문제로 잘못 지목됐다.

여기서 매 테스트가 끝날 때 쓸 수 있는 루프를 되돌려 놓으면 실행 순서와 무관하게 같은
결과가 나온다.
"""
import asyncio

import pytest


def _install_fresh_event_loop() -> None:
    """현재 스레드에 새 이벤트 루프를 놓는다. 쓸 수 없는 루프가 있으면 치운다."""
    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        loop = None

    if loop is not None and not loop.is_closed():
        return

    asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture(autouse=True)
def restore_event_loop():
    """테스트 앞뒤로 쓸 수 있는 이벤트 루프를 보장한다.

    앞에서도 한 번 보정하는 이유는, 오염을 남기는 쪽이 이 픽스처를 거치지 않는 경로일 수
    있기 때문이다(수집 중 실행되는 코드, 세션 범위 픽스처 등).
    """
    _install_fresh_event_loop()
    yield
    _install_fresh_event_loop()
