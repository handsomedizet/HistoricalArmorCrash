# 계산 파트 (직접 코딩)

import matplotlib.pyplot as plt
import MakeAnimation
import math

def make_plot(func, range_start, range_end, steps):
    answer = []
    x=range_start
    for i in range(steps):
        x += (range_end - range_start) / steps
        y = func(x)
        answer.append(y)
    return answer

def get_accerelation(func, range_start, range_end, steps, g = 9.8, a = 0.01):
    answer = []
    x=range_start
    for i in range(steps):
        x += (range_end - range_start) / steps
        x_1 = x + a
        tansent = (func(x_1) - func(x)) / a
        accerelation = g * tansent / (1+tansent**2)**0.5*-1
        answer.append(accerelation)
    return answer

def get_positions(func, range_start, range_end, g = 9.8, deltaTime = 0.01):
    answer = []
    x=range_start
    x_velocity = 0.1
    accerelation = 0
    while x < range_end and x_velocity > 0:
        x_1 = x+deltaTime
        tansent = (func(x_1) - func(x)) / deltaTime
        angle = math.atan(tansent)
        accerelation = g * tansent / (1+tansent**2)**0.5*-1
        x_velocity += accerelation * math.cos(angle) * deltaTime
        x += x_velocity * deltaTime
        print(x)
        answer.append(x)
    return answer

def test_function(x):
    return x**3 * -0.1

plt.plot(make_plot(test_function, -10, 10, 100))
plt.plot(get_accerelation(test_function, -10, 10, 100))
# plt.show()

datas = get_positions(test_function, -10, 10, 100)

# MakeAnimation.make(datas, make_plot(test_function, -10, 10, len(datas)), 10)




# # LS-dyna 활용 시뮬레이션 파트(라이브러리는 AI 제작, 여기 코드는 직접 코딩)
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import armor_impact

data = armor_impact.predict_injury(
    "두정갑",  # 두정갑 / 플레이트 / 없음
    250.0,     # 속도 m/s
    80.0,      # 구경 mm
    3.8,       # 질량 kg
)
print(data)

plt.show()