# 단일 충돌 예측 함수

패키지를 설치한 뒤 네 가지 조건으로 한 건의 LS-DYNA 계산을 실행하고,
AI API에 바로 전달할 수 있는 Python 딕셔너리를 받을 수 있다.

```python
from armor_impact import predict_injury

data = predict_injury(
    "두정갑",       # "두정갑", "플레이트", "없음"
    250.0,          # 탄환 속도 (m/s)
    80.0,           # 탄환 구경 (mm)
    3.8,            # 탄환 무게 (kg)
)
```

반환값은 JSON 직렬화가 가능한 `dict`이며, 주요 묶음은 다음과 같다.

- `impact_conditions`: 함수에 넣은 충돌 조건과 초기 운동에너지
- `projectile_response`: 잔류 속도와 전달 에너지
- `armor_response`: 갑옷 변위. 갑옷이 없으면 값은 `None`
- `torso_response`: 흉부·복부 변형량, 압축률, V*C, 중심 가속도
- `simulation_quality`: 에너지 보존 지표와 해석 경고
- `injury_prediction_ready`: AI API에 전달할 핵심 결과가 준비됐는지 여부
- `prediction_result`: 후속 AI 채점 전에는 `status="not_scored"`

현재 스키마는 `injury-prediction-input/v2`다. 갑옷 센서 노드가 파손되면
`armor_peak_ap_displacement_mm`은 삭제 전 이력에서만 계산되고,
`armor_local_failure_detected`와 `armor_sensor_deletion_time_ms`가 함께 출력된다.
가속도는 원시 단일 노드 최고값인 `torso_center_peak_acceleration_raw_g`와
고주파 진동을 줄인 `torso_center_peak_acceleration_3ms_g`를 모두 제공한다.
대표 키 `torso_center_peak_acceleration_g`는 3 ms 값을 우선 사용한다.
입력 질량이 구경과 명목 재료 밀도로 계산한 구형 질량과 다르면
`projectile_mass_scale`과 `projectile_effective_density_kg_m3`에 실제 해석에
사용된 질량 보정값이 기록된다.

갑옷 이름은 `두정갑`, `플레이트`, `없음` 외에 `dujeong`, `plate`, `none`도
받는다. 기본 입사각과 충돌 위치는 모두 0이며, 필요한 경우 키워드 인자로
`yaw_deg`, `pitch_deg`, `impact_x_mm`, `impact_z_mm`, `mesh_scale`을 지정할 수 있다.

함수는 현재 작업 폴더나 프로젝트 루트의 `study.toml`을 자동으로 사용한다. 설정이
없을 때만 패키지 기본값으로 시스템 경로에서 LS-DYNA를 찾는다. 별도 설정을 사용할
때는 `config_path="다른설정.toml"` 또는 `ARMOR_IMPACT_CONFIG` 환경변수를 지정할 수
있다. 실행 파일을 찾지 못하거나 계산 결과가 불완전하면 `InjuryPredictionError`를
발생시킨다. 반환값은 균질 흉복부 대리 모델의 선별용 결과이며, 임상 진단이나 검증된
인체 상해 확률이 아니다.
