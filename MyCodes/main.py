# 계산 파트 (직접 코딩)

import matplotlib.pyplot as plt
from MakeAnimation import animate_point
import math
from injury_ai import analyze_injury_with_gemini
from ContentPrinter import OutputTranslation
from sympy import sympify, Symbol
from enum import Enum

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import armor_impact

class ArmorType(Enum):
    NoArmor = "없음"
    Plate = "플레이트"
    Dujeong = "두정갑"

def make_plot(func, range_start, range_end, steps = 100):
    answer = []
    x=range_start
    for i in range(steps):
        x += (range_end - range_start) / steps
        y = func(x)
        answer.append(y)
    return answer

def get_Xrange(range_start, range_end, steps = 100, dx = 0.01, isStepsMode=True):
    if isStepsMode:
        return [range_start + (range_end - range_start) * i / steps for i in range(steps)]
    else:
        return [range_start + i * dx for i in range(int((range_end - range_start) / dx))]

def get_accerelation(func, range_start, range_end, steps = 100, g = 9.8, a = 0.01):
    answer = []
    x=range_start 
    for i in range(steps):
        x += (range_end - range_start) / steps
        x_1 = x + a
        tansent = (func(x_1) - func(x)) / a
        accerelation = g * tansent / (1+tansent**2)**0.5*-1
        answer.append(accerelation)
    return answer

def get_velocity(func, range_start, range_end, g = 9.8, dx = 0.01, deltaTime = 0.01, returnBoth = False):
    answer = [[], []]
    x=range_start
    x_velocity = 0.01
    velocity = 0.01
    accerelation = 0
    while x < range_end and x_velocity > 0:
        x_1 = x+dx
        tansent = (func(x_1) - func(x)) / dx
        angle = math.atan(tansent)
        accerelation = g * math.sin(angle) * -1
        velocity += accerelation * deltaTime

        x_velocity = velocity * math.cos(angle)
        x += x_velocity * deltaTime
        answer[0].append(x)
        answer[1].append(velocity)
    if returnBoth: return answer
    else: return answer[1]

def get_positions(func, range_start, range_end, g = 9.8, dx = 0.01, deltaTime = 0.01):
    answer = []
    x=range_start
    x_velocity = 0.1
    velocity = 0.1
    accerelation = 0
    while x < range_end and x_velocity > 0:
        x_1 = x+dx
        tansent = (func(x_1) - func(x)) / dx
        angle = math.atan(tansent)
        accerelation = g * math.sin(angle) * -1
        velocity += accerelation * deltaTime
        x_velocity = velocity * math.cos(angle)
        x += x_velocity * deltaTime
        answer.append(x)
    return answer

def test_function(x):
    return x**3 * -0.01

# 값 입력
startX = -10
endX = 3
expr = 'x**2'
armor = ArmorType.NoArmor
caliber = 1800.0 # mm
weight = 99999 # kg

x = Symbol('x')
expr = sympify(expr)
f = lambda a: expr.subs(x, a)

xRangeSteps = get_Xrange(startX, endX, 100)
xRangeDx = get_Xrange(startX, endX, dx=0.01, isStepsMode=False)

graph_plots = make_plot(f, startX, endX)
graph_accerelation = get_accerelation(f, startX, endX)

graph_velocity = get_velocity(f, startX, endX, returnBoth=True)
graph_velocity_x = graph_velocity[0]
graph_velocity_y = graph_velocity[1]

plt.figure(num=1)
plt.plot(xRangeSteps, graph_plots, label='Graph of f(x)')
plt.plot(xRangeSteps, graph_accerelation, label='Acceleration')
plt.plot(graph_velocity_x, graph_velocity_y, label='Velocity')
plt.legend()

datas = get_positions(f, startX, endX)

print('최종 속도:', graph_velocity_y[-1], 'm/s')
print('최고 속도:', max(graph_velocity_y), 'm/s')
print('최고 가속도:', max(graph_accerelation), 'm/s')

print('기초 계산 완료. 시뮬레이션 중...')

if graph_velocity_y[-1] <= 0:
    print("속도가 0 이하입니다. 시뮬레이션을 진행할 수 없습니다.")
    sys.exit(1)

# print(get_velocity(f, startX, endX)[-1])
# print(math.sqrt(2*9.8*abs(f(endX) - f(startX))))

# LS-dyna 활용 시뮬레이션 파트(라이브러리는 AI 제작, 여기 코드는 직접 코딩)

data = armor_impact.predict_injury(
    armor.value,  # 두정갑 / 플레이트 / 없음
    graph_velocity_y[-1],     # 속도 m/s
    caliber,   # 구경 mm
    weight     # 질량 kg
)
print(data)

print('시뮬레이션 완료. 결과 분석 중...')

simulation_result = data

result = analyze_injury_with_gemini(simulation_result)

OutputTranslation(result)

animate_point([(i, f(i)) for i in datas])