# LS-DYNA 역사 갑옷–흉복부 충돌 자동화 파이프라인

Windows 로컬 PC에서 구형 대포알이 갑옷을 거쳐 흉부·복부에 전달하는 충격을 반복 해석하기 위한 저비용 LS-DYNA 파이프라인이다. Python 표준 라이브러리만으로 입력 덱 생성, 순차 실행, 종료 판정, NODOUT/GLSTAT 후처리와 CSV 요약을 수행한다.

이 프로젝트의 기본 인체는 **검증된 인체 모델이 아니라 균질 점탄성 흉복부 대용체**다. 따라서 기본 결과는 조건 선별과 모델 개발용 대리지표이며, 실제 부상등급·사망률 또는 의료 판정으로 사용할 수 없다.

## 포함된 기능

- 판금갑옷과 두정갑 등가패널 교체
- 구경, 속도, 좌우 입사각, 상하 입사각, 충돌 위치의 조합 자동 생성
- 구경과 주철 밀도에서 대포알 질량 자동 계산
- 구형 메시 체적 오차가 질량을 바꾸지 않도록 대포알 밀도 자동 보정
- 저사양 PC를 위한 순차 LS-DYNA 실행
- 정상 종료, 오류 종료, 제한시간 초과 자동 구분
- 흉복부 압궤량, 압궤율, V*C 대리지표, 중심 가속도, 갑옷 변위 산출
- 대포알 잔류속도와 운동에너지 변화 산출
- GLSTAT 에너지비와 hourglass/internal energy 비율 검사
- 메시 배율을 이용한 수렴성 해석
- 갑옷 종류·속도·구경·질량으로 단일 계산을 실행하고 AI 입력용 딕셔너리 반환

## 라이브러리 호출

```python
from armor_impact import predict_injury

data = predict_injury("두정갑", 250.0, 80.0, 3.8)
```

필수 인자는 순서대로 갑옷 종류, 탄환 속도(m/s), 구경(mm), 질량(kg)이다.
갑옷 종류에는 `두정갑`, `플레이트`, `없음`을 사용할 수 있다. 자세한 반환 구조와
설정 방법은 [LIBRARY_API.md](LIBRARY_API.md)를 참고한다.

## 1. 빠른 시작

PowerShell에서 프로젝트 폴더로 이동한 후 실행한다.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item study.example.toml study.toml
python pipeline.py doctor --config study.toml
```

`study.toml`에서 실제 LS-DYNA 실행 파일 경로를 지정한다.

```toml
[solver]
executable = "C:\\LS-DYNA\\ls-dyna_smp_d.exe"
ncpus = 2
memory_mb = 2048
timeout_minutes = 120
```

입력 덱을 생성하고 실행한다.

```powershell
python pipeline.py build --config study.toml --study-dir runs
python pipeline.py run --config study.toml --study-dir runs --dry-run
python pipeline.py run --config study.toml --study-dir runs
python pipeline.py analyze --config study.toml --study-dir runs
```

한 번에 처리하려면 다음 명령을 쓸 수 있다.

```powershell
python pipeline.py all --config study.toml --study-dir runs
```

패키지 명령으로 설치할 수도 있다.

```powershell
python -m pip install -e .
armor-impact doctor --config study.toml
```

## 2. 사용자 입력

`[study]`의 모든 배열은 데카르트 곱으로 조합된다.

```toml
[study]
armor_types = ["plate", "dujeong_equivalent"]
caliber_mm = [80.0, 120.0]
speed_mps = [150.0, 250.0]
yaw_deg = [0.0, 15.0]
pitch_deg = [0.0, -10.0]
impact_x_mm = [0.0]
impact_z_mm = [0.0, 100.0]
mesh_scale = [1.0]
```

- `yaw_deg = 0`, `pitch_deg = 0`이면 대포알이 인체 정면에서 뒤쪽인 `+Y` 방향으로 진행한다.
- 양의 yaw는 `+X`, 양의 pitch는 `+Z` 방향으로 기울어진다.
- `impact_x_mm`, `impact_z_mm`는 흉복부 정면 중심을 원점으로 한다.
- 구경만 입력하면 구형 대포알과 `[projectile].density_kg_m3`를 사용해 질량을 계산한다.
- 실제 포탄 질량이 알려져 있다면 밀도를 `질량 / 구형 체적`에 맞춰 조정한다.

조건 수는 각 배열 길이의 곱이다. 저사양 PC에서는 먼저 갑옷 2종, 구경 2개, 속도 2개처럼 8개 안팎으로 시작하는 편이 안전하다.

## 3. 모델 구성

### 흉복부

기본 대용체는 360 × 200 × 500 mm의 육면체 솔리드 메시와 `*MAT_VISCOELASTIC`로 구성된다. 밀도, 체적탄성률, 단기·장기 전단탄성률과 감쇠상수는 모두 `study.toml`에서 변경할 수 있다.

이 값들은 실행 가능한 예시값이지 인체 검증값이 아니다. 늑골, 흉골, 피부, 폐, 간 같은 해부학적 구조를 구분하지 않으므로 장기 변형률이나 골절 위험을 직접 예측하지 못한다.

### 판금갑옷

연속 강재 셸과 `*MAT_PLASTIC_KINEMATIC`으로 근사한다. 두께, 탄성계수, 항복응력, 접선계수와 파단변형률을 입력할 수 있다.

### 두정갑

현재 버전은 직물과 내부 철판을 하나의 균질 셸로 합친 `dujeong_equivalent` 모델이다. 작은 철판의 겹침, 리벳, 직물 찢김, 철판 이탈을 재현하지 않는다. 실제 두정갑 비교를 하려면 먼저 철판–직물 시험편을 만들고 힘–변위 및 잔류속도 자료에 맞춰 등가 물성을 보정해야 한다.

### 대포알

정이십면체 기반 구면을 중심점과 연결한 4절점 테트라 메시다. 기본값은 변형 가능한 탄성 주철 근사다. 철구의 파쇄나 소성변형이 핵심이면 별도의 파단 재료카드로 교체해야 한다.

## 4. 결과 파일

```text
runs/
├─ manifest.csv
├─ summary.csv
└─ c0001_plate_d80_v150_y0_p0_m1/
   ├─ run.k
   ├─ case.json
   ├─ solver.log
   ├─ d3plot...
   ├─ nodout
   ├─ glstat
   ├─ metrics.json
   └─ impact_history.csv
```

`summary.csv`의 주요 필드는 다음과 같다.

| 필드 | 의미 |
|---|---|
| `max_deflection_mm` | 충돌 위치 전면–후면 노드의 최대 상대변위 |
| `max_compression_ratio` | 상대변위를 초기 흉복부 깊이로 나눈 값 |
| `peak_vc_mps` | 압궤율과 압궤속도의 곱인 V*C 대리지표 |
| `torso_center_peak_acceleration_g` | 대용체 중심 노드의 3 ms 벡터 평균 가속도 피크(이력 부족 시 원시값) |
| `projectile_residual_speed_mps` | 마지막 10% 시점의 대포알 중심 속도 중앙값 |
| `projectile_energy_change_j` | 초기 운동에너지에서 잔류 운동에너지를 뺀 값 |
| `final_energy_ratio` | GLSTAT의 최종 에너지비 |

V*C와 압궤량이라는 이름을 사용하지만, 이 대용체에서 얻은 값에 자동차 충돌 더미의 상해 임계값을 그대로 적용하면 안 된다. 센서 위치, 필터, 흉곽 구조와 검증 조건이 다르기 때문이다.

## 5. 반드시 수행할 검증

1. `d3hsp`, `solver.log`, LS-PrePost에서 초기 관통과 음의 체적 오류를 확인한다.
2. `final_energy_ratio`가 1에 충분히 가깝고 접촉에너지와 hourglass energy가 과도하지 않은지 확인한다. 파이프라인은 에너지비 오차 5% 초과와 hourglass/internal energy 10% 초과를 경고한다. 이 수치는 합격 보증이 아니라 검토 시작 기준이다.
3. 대포알–갑옷 시험편만으로 잔류속도, 함몰량과 파손 형태를 먼저 보정한다.
4. 중요한 케이스에 `mesh_scale = [1.0, 1.5, 2.0]`을 적용한다. 핵심 결과 변화가 충분히 작아질 때까지 메시를 세분한다.
5. 최종 상해 연구에서는 THUMS 같은 검증된 인체 모델을 별도로 받아 대체하고, 해당 모델 문서에 정의된 센서·상해 기준을 사용한다.

## 6. THUMS로 확장할 때

Toyota THUMS의 서 있는 성인 모델을 별도로 등록·다운로드한 뒤 다음 계층을 교체한다.

1. `armor_impact/mesh.py`의 균질 몸통 생성을 끈다.
2. THUMS keyword 파일을 `*INCLUDE` 또는 `*INCLUDE_TRANSFORM`으로 연결한다.
3. 갑옷의 위치와 단위를 THUMS 모델에 맞춘다.
4. `case.json`의 센서 이름을 THUMS의 흉골, 척추, 복부와 장기 ID에 매핑한다.
5. THUMS 설명서의 검증 범위와 상해 기준만 사용한다.

THUMS 자체는 이 프로젝트에 포함되어 있지 않다. Toyota의 배포 조건을 따라야 한다.

## 7. 테스트

LS-DYNA가 없어도 덱 생성과 ASCII 파서를 테스트할 수 있다.

```powershell
python -m unittest discover -s tests -v
```

실제 솔버 검증은 라이선스가 설정된 PC에서 `run.k`를 LS-PrePost로 먼저 열어 모델 검사 후 수행한다.

## 공식 참고자료

- [LS-DYNA R16 Keyword User's Manual, Volume I](https://lsdyna.ansys.com/wp-content/uploads/2025/04/LS-DYNA_Manual_Vol_I_R16.pdf)
- [LS-DYNA ASCII 결과와 HIC/CSI 인터페이스](https://lsdyna.ansys.com/ascii/)
- [LS-DYNA GLSTAT 에너지 구성](https://lsdyna.ansys.com/total-energy/)
- [Toyota THUMS 다운로드](https://www.toyota.co.jp/thums/)
- [Toyota THUMS 모델 구성과 검증 범위](https://www.toyota.co.jp/thums/about/)
