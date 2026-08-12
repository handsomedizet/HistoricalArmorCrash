import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

def make(x_data, y_data, interval=10):
    # 2. 플롯 및 객체 초기화
    fig, ax = plt.subplots(figsize=(6, 6))

    # 누적되는 선(line)과 현재 위치를 나타낼 점(point) 생성
    line, = ax.plot([], [], lw=2, color='blue', label='Trajectory')
    point, = ax.plot([], [], 'ro', ms=8, label='Current Pos') # 빨간 점

    # 데이터의 최솟값/최댓값을 기준으로 축 범위 자동 고정
    ax.set_xlim(np.min(x_data) - 0.5, np.max(x_data) + 0.5)
    ax.set_ylim(np.min(y_data) - 0.5, np.max(y_data) + 0.5)
    ax.grid(True)
    ax.legend()

    # 3. 초기화 함수
    def init():
        line.set_data([], [])
        point.set_data([], [])
        return line, point

    # 4. 프레임 업데이트 함수
    def update(frame):
        # frame 변수는 0부터 (전체 데이터 개수 - 1)까지 1씩 증가합니다.
        
        # 현재 프레임까지의 데이터를 잘라서 선으로 연결 (누적 효과)
        line.set_data(x_data[:frame], y_data[:frame])
        
        # 현재 프레임의 단일 x, y 좌표에 점 찍기
        point.set_data([x_data[frame]], [y_data[frame]])
        
        return line, point

    # 5. 애니메이션 객체 생성
    # interval=10 은 10ms(0.01초)마다 프레임을 업데이트하라는 뜻입니다.
    ani = FuncAnimation(
        fig, 
        update, 
        frames=len(x_data), 
        init_func=init, 
        blit=True, 
        interval=10
    )

    plt.show()