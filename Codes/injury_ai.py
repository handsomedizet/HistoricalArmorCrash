import os
import json
from enum import Enum
from typing import List

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from google import genai
from google.genai import types


# ============================================================
# 1. Gemini 출력 Schema
# ============================================================

class Level(str, Enum):
    LOW = "Low"
    MODERATE = "Moderate"
    HIGH = "High"
    CRITICAL = "Critical"


class Specificity(str, Enum):
    REASONABLY_SUPPORTED = "reasonably_supported"
    MECHANISTIC_HYPOTHESIS = "mechanistic_hypothesis"
    SPECULATIVE = "speculative"


class MechanicalFinding(BaseModel):
    finding: str = Field(
        description="시뮬레이션 결과에서 직접 지지되는 기계적 현상"
    )

    concern_level: Level

    evidence_paths: List[str]


class PossibleInjury(BaseModel):
    injury_name: str = Field(
        description=(
            "실제 인간에서 발생할 수 있는 구체적인 부상명. "
            "필요하면 늑골 골절, 폐좌상, 혈종, 장기 손상 등 "
            "구체적인 명칭을 사용할 수 있다."
        )
    )

    anatomical_region: str

    plausibility: Level

    expected_severity: str = Field(
        description=(
            "해당 손상이 실제 발생했다고 가정할 경우 예상되는 "
            "정성적 중증도 범위"
        )
    )

    specificity: Specificity = Field(
        description=(
            "현재 surrogate가 이 부상을 어느 정도 구체적으로 "
            "뒷받침할 수 있는지"
        )
    )

    evidence_paths: List[str]

    reasoning: str

    cannot_confirm_because: str


class LessSupportedInjury(BaseModel):
    injury_name: str
    reason: str


class PerforationAssessment(BaseModel):
    status: str = Field(
        description="Confirmed, Not detected, Unknown 중 하나"
    )

    reason: str


class InjuryAssessment(BaseModel):
    overall_mechanical_concern: Level

    human_injury_severity_estimate: str

    human_injury_severity_confidence: Level

    mechanical_findings: List[MechanicalFinding]

    possible_injuries: List[PossibleInjury]

    less_supported_injuries: List[LessSupportedInjury]

    perforation_assessment: PerforationAssessment

    summary: str


# ============================================================
# 2. API 설정
# ============================================================

load_dotenv()

# 기존 GCP_API_KEY를 써도 되고,
# .env를 GEMINI_API_KEY로 바꿔도 작동하도록 처리
API_KEY = (
    os.getenv("GEMINI_API_KEY")
    or os.getenv("GCP_API_KEY")
)

if not API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY 또는 GCP_API_KEY가 .env에 설정되어 있지 않습니다."
    )

client = genai.Client(
    api_key=API_KEY,
    http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(
            attempts=5,
            initial_delay=2.0,
            max_delay=30.0,
            exp_base=2,
            jitter=1.0,
            http_status_codes=[
                408,
                429,
                500,
                502,
                503,
                504,
            ],
        )
    ),
)


# ============================================================
# 3. LS-DYNA v3 출력에서 Gemini가 사용할 데이터 추출
# ============================================================

def build_gemini_injury_input(sim: dict) -> dict:
    """
    injury-prediction-input/v3 dict에서
    Gemini injury screening에 필요한 정보를 추린다.

    물리량 계산은 여기서 새로 하지 않는다.
    LS-DYNA 후처리 코드가 계산한 결과만 전달한다.
    """

    return {
        "schema_version": sim.get("schema_version"),
        "prediction_task": sim.get("prediction_task"),

        "model_context": {
            "model_type":
                sim["model_context"].get("model_type"),

            "screening_only":
                sim["model_context"].get("screening_only"),

            "surrogate_geometry":
                sim["model_context"].get("surrogate_geometry"),

            "metric_definitions":
                sim["model_context"].get("metric_definitions"),

            "limitations":
                sim["model_context"].get("limitations", []),
        },

        "impact_conditions":
            sim.get("impact_conditions", {}),

        "projectile_response":
            sim.get("projectile_response", {}),

        "armor_response":
            sim.get("armor_response", {}),

        "torso_response":
            sim.get("torso_response", {}),

        "simulation_quality": {
            "analysis_status":
                sim["simulation_quality"].get("analysis_status"),

            "simulation_duration_ms":
                sim["simulation_quality"].get("simulation_duration_ms"),

            "final_energy_ratio":
                sim["simulation_quality"].get("final_energy_ratio"),

            "final_energy_ratio_without_eroded":
                sim["simulation_quality"].get(
                    "final_energy_ratio_without_eroded"
                ),

            "final_hourglass_to_internal_ratio":
                sim["simulation_quality"].get(
                    "final_hourglass_to_internal_ratio"
                ),

            "validation_status":
                sim["simulation_quality"].get("validation_status"),

            "warnings":
                sim["simulation_quality"].get("warnings", []),

            "validation_errors":
                sim["simulation_quality"].get(
                    "validation_errors", []
                ),
        },
    }


# ============================================================
# 4. Gemini가 제시한 evidence path 검증
# ============================================================

def resolve_feature_path(data: dict, path: str):
    """
    'torso_response.impact_site.peak_vc_mps'
    같은 dotted path를 실제 dict 값으로 변환한다.
    """

    current = data

    for key in path.split("."):
        if not isinstance(current, dict):
            raise KeyError(path)

        if key not in current:
            raise KeyError(path)

        current = current[key]

    return current


def validate_evidence_paths(
    assessment: InjuryAssessment,
    source_data: dict,
):
    """
    Gemini가 존재하지 않는 feature path를
    근거로 사용하지 않았는지 검증한다.
    """

    # 1. Mechanical findings 검증
    for finding in assessment.mechanical_findings:

        if not finding.evidence_paths:
            raise ValueError(
                f"{finding.finding}: evidence_paths가 비어 있습니다."
            )

        for path in finding.evidence_paths:
            try:
                resolve_feature_path(source_data, path)

            except KeyError:
                raise ValueError(
                    f"Gemini가 존재하지 않는 feature를 "
                    f"mechanical finding 근거로 사용했습니다: {path}"
                )

    # 2. Possible injuries 검증
    for injury in assessment.possible_injuries:

        if not injury.evidence_paths:
            raise ValueError(
                f"{injury.injury_name}: evidence_paths가 비어 있습니다."
            )

        for path in injury.evidence_paths:
            try:
                resolve_feature_path(source_data, path)

            except KeyError:
                raise ValueError(
                    f"Gemini가 존재하지 않는 feature를 "
                    f"부상 근거로 사용했습니다: {path}"
                )


# ============================================================
# 5. 필드명에서 단위 추정
# ============================================================

UNIT_SUFFIXES = (
    ("_kg_m3", "kg/m^3"),
    ("_gpa", "GPa"),
    ("_mpa", "MPa"),
    ("_mps", "m/s"),
    ("_mm", "mm"),
    ("_ms", "ms"),
    ("_kg", "kg"),
    ("_kn", "kN"),
    ("_j", "J"),
    ("_g", "g"),
)


def infer_unit(feature_path: str):
    name = feature_path.split(".")[-1].lower()

    for suffix, unit in UNIT_SUFFIXES:
        if name.endswith(suffix):
            return unit

    if (
        name.endswith("_ratio")
        or name.endswith("_fraction")
        or name.endswith("_scale")
    ):
        return "dimensionless"

    return None


# ============================================================
# 6. Gemini system instruction
# ============================================================

SYSTEM_INSTRUCTION = """
당신은 LS-DYNA 충격 시뮬레이션 결과를 해석하는 생체역학 injury-risk screening 분석기입니다.

입력 데이터는 실제 환자 데이터가 아니라, homogeneous viscoelastic torso surrogate를 이용한 유한요소 시뮬레이션의 후처리 결과입니다.

이 surrogate에는 실제 인간의 갈비뼈, 폐, 심장, 간, 비장, 혈관, 피부, 근육 등의 해부학적 구조가 명시적으로 구현되어 있지 않을 수 있습니다.

당신의 목표는 임상적 확진을 내리는 것이 아니라, 제공된 기계적 response를 실제 인간에게 대응시켰을 때 발생 가능성이 있는 구체적인 손상 후보를 생체역학적으로 추론하는 것입니다.

## 핵심 원칙

1. 입력 데이터에 존재하는 수치와 simulation feature만 사실로 취급하세요.

2. 입력에 없는 force, acceleration, displacement, velocity, energy, threshold, 확률 등의 값을 새로 만들지 마세요.

3. 구체적인 인간 부상명을 제시하는 것은 허용되며 권장됩니다.

예를 들어 기계적 하중 양상이 적절하다면 다음과 같은 구체적인 손상 후보를 제시할 수 있습니다.

- 흉벽 좌상
- 심부 연조직 손상
- 늑골 골절
- 폐좌상
- 기흉
- 혈흉
- 국소 혈종
- 간 손상
- 비장 손상
- 기타 흉복부 장기 손상

그러나 해당 해부학적 구조가 surrogate에 직접 구현되어 있지 않다면 이러한 부상을 확진된 손상처럼 표현해서는 안 됩니다.

4. 각 부상 후보를 다음 세 수준 중 하나로 구분하세요.

### reasonably_supported

현재 simulation metric과 해당 부상 사이의 기계적 연관성이 비교적 직접적이며, 현재 모델 수준에서도 어느 정도 구체적으로 지지되는 경우입니다.

### mechanistic_hypothesis

실제 인간이라면 현재 충격 메커니즘으로 발생 가능한 구체적 손상이지만, 해당 장기나 골격이 surrogate에 직접 구현되어 있지 않아 시뮬레이션 자체로 확인할 수 없는 경우입니다.

### speculative

기계적으로 가능하기는 하지만 현재 데이터와의 연결이 약하거나 추가적인 조건이 많이 필요한 경우입니다.

가능한 한 `reasonably_supported` 또는 `mechanistic_hypothesis` 수준의 부상에 집중하고, 단순히 발생 가능한 모든 최악의 손상을 나열하지 마세요.

5. `possible_injuries`에는 원칙적으로 plausibility가 Moderate 이상인 부상 후보만 포함하세요.

Low로 판단되는 후보는 `less_supported_injuries`에 넣으세요.

6. broad category만 반복하지 말고, 현재 데이터가 허용하는 범위에서 실제 인간에서 예상할 수 있는 구체적인 부상명을 제시하세요.

단, 구체적인 부상명과 그것이 확정되었다는 주장은 엄격하게 구별하세요.

예:

좋음:
"늑골 골절은 기계적으로 가능한 손상 후보이나, 현재 surrogate에는 늑골 구조가 없어 직접 확인할 수 없다."

나쁨:
"늑골 골절이 발생했다."

7. 부상 발생 확률을 0.83, 75% 등의 숫자로 만들어내지 마세요.

현재 입력에는 임상적으로 calibration된 injury probability model이 없으므로 확률은 다음 정성적 수준만 사용하세요.

- Low
- Moderate
- High

8. AIS 점수를 직접 생성하지 마세요.

현재 simulation은 확인된 실제 해부학적 손상 데이터를 제공하지 않으므로 AIS를 직접 산출하기에 충분하지 않습니다.

9. simulation에서 직접 관찰되는 기계적 사실과 실제 인간 손상에 대한 추론을 반드시 구별하세요.

분석 논리는 다음 세 단계로 구성하세요.

### 단계 A: 직접 관찰된 mechanical response

예:

- impact-site deflection
- compression ratio
- peak VC
- chest regional response
- abdomen regional response
- 3 ms acceleration
- armor local failure
- projectile kinetic-energy loss

### 단계 B: mechanical interpretation

예:

- 국소 고속 압축
- 충격점 중심의 강한 변형
- 전역 흉부 압축은 상대적으로 작음
- 복부 전체 response는 제한적
- 높은 acceleration loading

### 단계 C: possible human injuries

이 mechanical interpretation을 실제 인간에게 적용할 경우 예상 가능한 부상 후보를 제시합니다.

10. local impact-site metric과 regional chest/abdomen metric을 동일하게 취급하지 마세요.

예를 들어 impact-site VC가 높고 chest regional VC가 낮다면:

"흉부 전체가 동일한 수준으로 압축되었다"

라고 결론 내리지 마세요.

대신:

"충격점에서 강한 국소 response가 발생했지만, 선택된 regional chest measurement에서는 비교적 작은 response가 나타났다."

와 같이 공간적 차이를 분석하세요.

11. 단일 node 또는 단일 front/back node pair 측정이 전체 흉부나 복부를 완전히 대표한다고 가정하지 마세요.

측정 provenance와 model limitations를 해석에 반영하세요.

12. raw nodal acceleration과 screening용 filtered/averaged acceleration을 구분하세요.

입력에서 `preferred_screening_metric`이 지정되어 있다면 이를 우선 사용하세요.

특히 raw acceleration에 numerical spike가 있다는 warning이 있으면 raw peak만으로 부상의 심각도를 판단하지 마세요.

13. `armor_local_failure_detected`와 `armor_perforation_detected`는 서로 다른 개념입니다.

갑옷의 국소 파손 또는 element erosion만으로 완전 관통을 확정하지 마세요.

`armor_perforation_detected`가 `None`이거나 status가 `not_determined`라면:

- 관통되었다고 말하지 마세요.
- 비관통이라고도 말하지 마세요.

반드시 관통 여부가 불명확하다고 표현하세요.

14. 따라서 `armor_perforation_detected`가 불명확한 경우 dominant mechanism을 임의로 "비관통 둔상" 또는 "관통상"으로 단정하지 마세요.

대신 다음과 같이 표현할 수 있습니다.

"고속 발사체 충격에 따른 강한 국소 기계적 loading"

또는

"갑옷 국소 파손을 동반한 국소 고속 충격"

15. armor failure가 발생했다는 사실만으로:

"에너지가 인체에 집중 전달되었다"

같은 인과관계를 단정하지 마세요.

실제 energy metric 또는 torso response가 이를 뒷받침해야 합니다.

16. projectile residual velocity가 single mesh node proxy라면 이를 projectile center-of-mass velocity 또는 완전한 projectile residual state로 표현하지 마세요.

17. 외부의 자동차 충돌 규정, FMVSS 기준, 임상 threshold, 특정 AIS risk curve 등을 입력 데이터에 근거 없이 자동 적용하지 마세요.

외부 기준과의 정량적 비교가 명시적으로 제공되지 않았다면 현재 simulation feature 자체의 상대적인 기계적 response를 중심으로 판단하세요.

18. VC, compression, acceleration 등의 값에 대해 제공되지 않은 임상적 cutoff를 만들어내지 마세요.

19. severity의 의미를 명확히 구분하세요.

`overall_mechanical_concern`은 simulation이 보여주는 기계적 loading 자체의 우려 수준입니다.

`expected_severity`는 특정 부상 후보가 실제로 발생했다고 가정할 경우 예상되는 정성적 손상 심각도 범위입니다.

이 둘을 혼동하지 마세요.

20. `human_injury_severity_estimate`는 실제 임상 severity 확정값이 아니라 가능한 인간 손상들을 종합한 screening 수준의 추정입니다.

모델의 해부학적 한계가 크다면 confidence를 낮추세요.

21. 각 가능한 부상 후보에는 반드시 실제 입력 dictionary에 존재하는 `evidence_paths`를 지정하세요.

예:

`torso_response.impact_site.peak_vc_mps`

`torso_response.impact_site.max_deflection_mm`

`torso_response.chest.max_compression_ratio`

`torso_response.torso_center_acceleration.vector_average_3ms_peak_g`

존재하지 않는 feature path를 만들지 마세요.

22. evidence에 포함된 숫자를 새로 생성하지 마세요.

후속 Python 코드가 `evidence_paths`를 이용해 실제 simulation dict에서 원본 값을 가져올 것이므로, 당신은 feature path를 선택하고 그 의미를 해석하는 데 집중하세요.

23. `mechanical_findings`에는 simulation 결과에서 직접적으로 지지되는 기계적 현상만 기록하세요.

예:

- 충격점 중심의 높은 국소 VC
- 국소 변형이 regional chest/abdomen 변형보다 큼
- 갑옷 국소 파손 감지
- 높은 3 ms center-node acceleration
- 복부 regional response가 상대적으로 낮음

24. `possible_injuries`에서는 구체적인 실제 인간 부상 후보를 제시하세요.

다만 각 항목에 다음을 반드시 포함하세요.

- injury_name
- anatomical_region
- plausibility
- expected_severity
- specificity
- evidence_paths
- reasoning
- cannot_confirm_because

25. `reasoning`에서는 단순히 숫자를 반복하지 말고, 해당 mechanical response와 부상 후보 사이의 생체역학적 연결을 설명하세요.

그러나 현재 데이터가 지원하는 범위를 넘어 인과관계를 확정하지 마세요.

26. `cannot_confirm_because`에서는 현재 surrogate가 해당 부상을 직접 확인할 수 없는 핵심 이유를 구체적으로 적으세요.

예:

- explicit rib geometry가 없음
- lung parenchyma가 별도 material로 존재하지 않음
- vascular structure가 구현되지 않음
- regional measurement가 single node pair임
- armor perforation 여부가 확인되지 않음

27. `less_supported_injuries`에는 현재 response가 상대적으로 낮거나, 기계적으로 필요한 근거가 부족한 손상을 기록할 수 있습니다.

단순히 목록을 채우기 위해 억지로 추가하지 마세요.

28. simulation quality warning을 반드시 고려하세요.

validation failure나 심각한 numerical problem이 있다면 confidence를 낮추고 summary에 명시하세요.

29. homogeneous viscoelastic torso surrogate라는 한계를 항상 고려하세요.

이는 특정 장기의 파열, 특정 늑골의 골절, 혈관 손상 등을 직접 계산하는 human body model이 아닙니다.

30. 그러나 모델의 한계를 이유로 모든 결과를 "연부조직 손상 가능성" 같은 모호한 표현 하나로 축소하지 마세요.

현재 기계적 response와 실제 인간 생체역학을 연결하여, 합리적인 범위 안에서 구체적인 손상 후보를 적극적으로 제시하세요.

31. 확실하지 않은 것은 확실하지 않다고 명시하되, 가능한 부상 후보 자체를 회피하지 마세요.

32. 분석 결과는 과도하게 방어적인 문구의 반복보다 정보량을 우선하세요.

각 후보가:

- 왜 가능한지
- 어느 정도로 가능한지
- 어느 정도 심각할 수 있는지
- 왜 확정할 수 없는지

를 명확하게 설명하세요.

33. 모든 자연어 설명은 한국어로 작성하세요.

34. 결과는 제공된 Pydantic schema를 정확히 따르세요.

## 분석의 최종 목표

최종 결과를 읽는 사람이 다음을 빠르게 이해할 수 있어야 합니다.

1. 시뮬레이션에서 실제로 어떤 기계적 현상이 발생했는가?
2. 가장 우려되는 기계적 loading은 무엇인가?
3. 실제 인간이라면 어떤 구체적인 부상이 발생할 수 있는가?
4. 그중 어떤 부상이 더 강하게 지지되는가?
5. 어떤 심각도가 예상되는가?
6. 어떤 부상은 현재 데이터로 덜 지지되는가?
7. 갑옷 관통 여부는 확인되었는가?
8. 이 예측의 가장 중요한 불확실성과 모델 한계는 무엇인가?

안전한 표현만 반복하는 것이 목적이 아닙니다.

**목적은 현재 LS-DYNA simulation이 제공하는 정보를 최대한 활용하여 구체적이고 유용한 인간 부상 후보를 제시하면서, 직접 관찰된 사실과 생체역학적 추론의 경계를 명확하게 유지하는 것입니다.**
"""


# ============================================================
# 7. Gemini 호출
# ============================================================

def analyze_injury_with_gemini(
    simulation_result: dict,
) -> dict:

    gemini_input = build_gemini_injury_input(simulation_result)

    prompt = (
        "다음 LS-DYNA 흉복부 충격 simulation feature를 "
        "분석하여 injury-risk screening을 수행하세요.\n\n"
        "입력 데이터:\n"
        + json.dumps(
            gemini_input,
            ensure_ascii=False,
            indent=2,
        )
    )

    print("[Gemini] 요청 시작")

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=InjuryAssessment,
            temperature=0.1,

            automatic_function_calling=
                types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
        ),
    )

    print("[Gemini] 응답 수신")

    print("[Gemini] response.text 존재 여부:",
          bool(response.text))

    print("[Gemini] response.parsed 타입:",
          type(response.parsed))

    assessment = response.parsed

    if assessment is None:
        print("[Gemini] parsed 없음 → response.text 직접 파싱")

        assessment = InjuryAssessment.model_validate_json(
            response.text
        )

    print("[Gemini] Pydantic 파싱 완료")

    validate_evidence_paths(
        assessment,
        gemini_input,
    )

    print("[Gemini] evidence path 검증 완료")

    result = assessment.model_dump(mode="json")

    print("[Gemini] dict 변환 완료")

    for finding in result["mechanical_findings"]:
        verified_evidence = []

        for path in finding["evidence_paths"]:
            value = resolve_feature_path(
                gemini_input,
                path,
            )

            verified_evidence.append({
                "feature_path": path,
                "value": value,
                "unit": infer_unit(path),
            })

        finding["evidence"] = verified_evidence
        del finding["evidence_paths"]

    print("[Gemini] mechanical_findings 가공 완료")

    for injury in result["possible_injuries"]:
        verified_evidence = []

        for path in injury["evidence_paths"]:
            value = resolve_feature_path(
                gemini_input,
                path,
            )

            verified_evidence.append({
                "feature_path": path,
                "value": value,
                "unit": infer_unit(path),
            })

        injury["evidence"] = verified_evidence
        del injury["evidence_paths"]

    print("[Gemini] possible_injuries 가공 완료")

    result["model_limitations"] = (
        gemini_input["model_context"]["limitations"]
    )

    result["clinical_diagnosis_possible"] = False

    print("[Gemini] 분석 완료")

    return result


# ============================================================
# 8. 사용 예
# ============================================================

# 네 LS-DYNA 후처리 코드가 반환한 실제 v3 dict를 사용한다.
#
# 예:
#
# simulation_result = run_lsdyna_and_postprocess(...)
#
# 또는 이미 변수에 결과가 있다면:
#
# simulation_result = injury_prediction_input


# assessment_result = analyze_injury_with_gemini(
#     simulation_result
# )

# print(
#     json.dumps(
#         assessment_result,
#         ensure_ascii=False,
#         indent=2,
#     )
# )