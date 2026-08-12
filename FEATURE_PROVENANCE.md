# Feature provenance

`injury-prediction-input/v3`는 LS-DYNA 결과를 임상 부상 판정이 아닌 균질 흉복부
대용체의 screening feature로 변환한다.

| 출력 그룹 | 원본 | 선택 및 변환 |
|---|---|---|
| Projectile residual | `nodout` | projectile 중심 메시 노드 한 개의 속도 크기. 마지막 10% frame의 median |
| Projectile residual KE | 파생값 | `0.5 * 요청 질량 * residual proxy speed^2` |
| Projectile KE loss | 파생값 | 초기 KE에서 residual proxy KE를 뺀 값. 인체 전달에너지와 동일시하지 않음 |
| Armor displacement | `nodout` | 충돌점에 가장 가까운 갑옷 단일 노드의 global +Y 변위. rigid translation 미제거 |
| Armor local failure | `solver.log`, `messag` | 추적 노드 삭제 또는 충돌점 인접 shell 요소 failure 메시지 |
| Torso deflection | `nodout` | 각 위치의 단일 front/back 표면 노드 global Y 변위 차 |
| Compression ratio | 파생값 | deflection / 초기 균질 torso depth |
| V*C proxy | 파생값 | compression ratio와 무필터 deflection 중앙차분 속도의 곱 중 양수 최대값 |
| Raw acceleration | `nodout` | torso 중심 단일 노드 acceleration 벡터 크기의 최대값 |
| 3 ms acceleration | `nodout` | sliding 3 ms 구간의 nodal velocity 변화 벡터 크기, endpoint 선형 보간 |
| Energy quality | `glstat` | 마지막 global GLSTAT frame의 실제 항목. 결측은 `None`이며 0으로 대체하지 않음 |

`impact_site`는 요청 충돌 위치에 가장 가까운 structured-mesh front/back 노드 쌍이다.
`chest`와 `abdomen`은 각각 `z=+0.20*height`, `z=-0.20*height`에 가장 가까운
중앙 front/back 노드 쌍이다. 영역 평균 또는 node-set 최대값이 아니다.

현재 저장된 nodal history만으로 projectile의 완전 관통을 안정적으로 판정하지 않는다.
따라서 armor가 있을 때 관통값은 `None`, 상태는
`not_determined_from_available_histories`다.
