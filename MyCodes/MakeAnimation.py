import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation


def animate_point(
    positions,
    dt=0.01,
    save_path=None,
    show=True,
    trail=True,
    point_size=8,
):
    """
    프레임별 좌표를 받아 점의 움직임을 애니메이션으로 출력한다.

    positions:
        [(x0, y0), (x1, y1), ...]
        각 원소가 한 프레임의 점 좌표.

    dt:
        프레임 간 시간(초). 기본값 0.01초.

    save_path:
        저장할 파일 경로.
        예: "result.mp4"
        None이면 저장하지 않음.

    show:
        True면 애니메이션 창 출력.

    trail:
        True면 점이 지나간 경로 표시.
    """

    if len(positions) == 0:
        raise ValueError("positions가 비어 있습니다.")

    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]

    fig, ax = plt.subplots()

    point, = ax.plot([], [], "o", markersize=point_size)

    if trail:
        path, = ax.plot([], [], "-", alpha=0.5)
    else:
        path = None

    # 화면 범위 계산
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    x_range = max_x - min_x
    y_range = max_y - min_y

    # 모든 좌표가 같아 범위가 0이 되는 경우 방지
    if x_range == 0:
        x_range = 1

    if y_range == 0:
        y_range = 1

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

    ax.set_aspect("equal", adjustable="box")
    ax.grid()

    def init():
        point.set_data([], [])

        if path is not None:
            path.set_data([], [])
            return point, path

        return (point,)

    def update(frame):
        x, y = positions[frame]

        point.set_data([x], [y])

        if path is not None:
            path.set_data(
                xs[:frame + 1],
                ys[:frame + 1]
            )

            return point, path

        return (point,)

    animation = FuncAnimation(
        fig,
        update,
        frames=len(positions),
        init_func=init,
        interval=dt * 1000,
        blit=True,
        repeat=False
    )

    if save_path is not None:
        fps = round(1 / dt)

        animation.save(
            save_path,
            writer="ffmpeg",
            fps=fps
        )

    if show:
        plt.show()

    return animation