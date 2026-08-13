import math
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation


def animate_point(
    positions,
    dt=0.01,
    save_path=None,
    show=True,
    trail=True,
    point_size=8,
    repeat=True,
):
    """
    프레임별 좌표를 받아 점의 움직임을 애니메이션으로 출력한다.

    Parameters
    ----------
    positions : iterable
        [(x0, y0), (x1, y1), ...] 형태의 좌표 목록.

    dt : float
        프레임 간 시간(초).
        기본값은 0.01초 = 100 FPS.

    save_path : str or None
        MP4 등으로 저장할 경로.
        예: "result.mp4"
        None이면 저장하지 않는다.

    show : bool
        True면 애니메이션 창을 띄운다.

    trail : bool
        True면 점이 지나온 경로를 표시한다.

    point_size : float
        점의 크기.

    repeat : bool
        True면 애니메이션을 반복 재생한다.

    Returns
    -------
    matplotlib.animation.FuncAnimation
        생성된 애니메이션 객체.
    """

    # generator 등도 받을 수 있도록 list로 변환
    positions = list(positions)

    if len(positions) == 0:
        raise ValueError("positions가 비어 있습니다.")

    # matplotlib과 numpy가 안정적으로 처리할 수 있도록
    # 모든 좌표를 일반 float로 변환
    converted_positions = []

    for index, position in enumerate(positions):

        if len(position) != 2:
            raise ValueError(
                f"{index}번째 좌표가 (x, y) 형태가 아닙니다: {position}"
            )

        x, y = position

        try:
            x = float(x)
            y = float(y)

        except (TypeError, ValueError) as e:
            raise TypeError(
                f"{index}번째 좌표를 float로 변환할 수 없습니다: "
                f"({x}, {y})"
            ) from e

        # NaN, inf 방지
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError(
                f"{index}번째 좌표가 유효하지 않습니다: "
                f"({x}, {y})"
            )

        converted_positions.append((x, y))

    positions = converted_positions

    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]

    # =========================
    # 그래프 생성
    # =========================

    fig, ax = plt.subplots()

    # 움직이는 점
    point, = ax.plot(
        [],
        [],
        "o",
        markersize=point_size
    )

    # 이동 경로
    if trail:
        path, = ax.plot(
            [],
            [],
            "-",
            alpha=0.5
        )
    else:
        path = None

    # =========================
    # 화면 범위 계산
    # =========================

    min_x = min(xs)
    max_x = max(xs)

    min_y = min(ys)
    max_y = max(ys)

    x_range = max_x - min_x
    y_range = max_y - min_y

    # 모든 값이 같은 경우 범위가 0이 되므로 보정
    if x_range == 0:
        x_range = max(abs(min_x), 1)

    if y_range == 0:
        y_range = max(abs(min_y), 1)

    margin_x = x_range * 0.1
    margin_y = y_range * 0.1

    ax.set_xlim(
        min_x - margin_x,
        max_x + margin_x
    )

    ax.set_ylim(
        min_y - margin_y,
        max_y + margin_y
    )

    ax.set_aspect(
        "equal",
        adjustable="box"
    )

    ax.grid(True)

    # =========================
    # 애니메이션 초기화
    # =========================

    def init():

        point.set_data([], [])

        if path is not None:
            path.set_data([], [])
            return point, path

        return (point,)

    # =========================
    # 매 프레임 업데이트
    # =========================

    def update(frame):

        x, y = positions[frame]

        point.set_data(
            [x],
            [y]
        )

        if path is not None:

            path.set_data(
                xs[:frame + 1],
                ys[:frame + 1]
            )

            return point, path

        return (point,)

    # =========================
    # 애니메이션 생성
    # =========================

    animation = FuncAnimation(
        fig,
        update,
        frames=len(positions),
        init_func=init,
        interval=dt * 1000,
        blit=True,
        repeat=repeat
    )

    # =========================
    # 파일 저장
    # =========================

    if save_path is not None:

        fps = round(1 / dt)

        animation.save(
            save_path,
            writer="ffmpeg",
            fps=fps
        )

    # =========================
    # 화면 출력
    # =========================

    if show:
        plt.show()

    return animation