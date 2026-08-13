d = {
    'overall_mechanical_concern': 'High',

    'human_injury_severity_estimate':
        '충격점 중심의 심각한 국소 연조직 손상 및 중증 흉부 둔상'
        '(늑골 골절, 폐좌상 등) 가능성',

    'human_injury_severity_confidence': 'Low',

    'mechanical_findings': [
        {
            'finding':
                '충격 지점에서 매우 높은 국소 점성 응답'
                '(Peak VC 3.77 m/s) 및 강한 국소 변형 발생',

            'concern_level': 'High',

            'evidence': [
                {
                    'feature_path':
                        'torso_response.impact_site.peak_vc_mps',
                    'value': 3.7689788011683243,
                    'unit': 'm/s'
                },
                {
                    'feature_path':
                        'torso_response.impact_site.max_deflection_mm',
                    'value': 14.665581,
                    'unit': 'mm'
                }
            ]
        },

        {
            'finding':
                '갑옷의 국소 파손(Element Failure/Deletion)이 '
                '충격 극초반(0.27 ms)에 감지됨',

            'concern_level': 'High',

            'evidence': [
                {
                    'feature_path':
                        'armor_response.armor_local_failure_detected',
                    'value': True,
                    'unit': None
                },
                {
                    'feature_path':
                        'armor_response.armor_local_failure_time_ms',
                    'value': 0.26886,
                    'unit': 'ms'
                }
            ]
        },

        {
            'finding':
                '토르소 중앙 노드에서 높은 3ms 이동평균 가속도 '
                '피크(666.1 g) 기록',

            'concern_level': 'High',

            'evidence': [
                {
                    'feature_path':
                        'torso_response.torso_center_acceleration.'
                        'vector_average_3ms_peak_g',
                    'value': 666.1052319838676,
                    'unit': 'g'
                }
            ]
        },

        {
            'finding':
                '충격 지점에 비해 흉부 및 복부 전역 응답'
                '(VC 0.17 m/s 이하, 압축률 2.2% 수준)은 '
                '비교적 제한적임',

            'concern_level': 'Moderate',

            'evidence': [
                {
                    'feature_path':
                        'torso_response.chest.peak_vc_mps',
                    'value': 0.16979280623987242,
                    'unit': 'm/s'
                },
                {
                    'feature_path':
                        'torso_response.chest.max_compression_ratio',
                    'value': 0.0222511,
                    'unit': 'dimensionless'
                },
                {
                    'feature_path':
                        'torso_response.abdomen.peak_vc_mps',
                    'value': 0.1567546822267864,
                    'unit': 'm/s'
                },
                {
                    'feature_path':
                        'torso_response.abdomen.max_compression_ratio',
                    'value': 0.021690199999999996,
                    'unit': 'dimensionless'
                }
            ]
        }
    ],

    'possible_injuries': [
        {
            'injury_name':
                '흉벽 심부 연조직 손상 및 국소 혈종',

            'anatomical_region':
                '흉부 (Chest Wall)',

            'plausibility':
                'High',

            'expected_severity':
                'Moderate to High',

            'specificity':
                'reasonably_supported',

            'reasoning':
                '충격 지점에서 3.77 m/s에 달하는 높은 Peak VC와 '
                '14.67 mm의 국소 변형이 관찰되었으며, 이는 타격 '
                '지점 직하의 피부 및 근육, 심부 연조직에 강한 압축 및 '
                '찰상을 유발할 기계적 조건에 해당합니다.',

            'cannot_confirm_because':
                '서로게이트 모델이 단일 균일 점탄성체'
                '(homogeneous viscoelastic surrogate)로 구현되어 있어 '
                '심부 연조직의 정확한 파열이나 조직 세부 손상을 직접 '
                '측정할 수 없습니다.',

            'evidence': [
                {
                    'feature_path':
                        'torso_response.impact_site.peak_vc_mps',
                    'value': 3.7689788011683243,
                    'unit': 'm/s'
                },
                {
                    'feature_path':
                        'torso_response.impact_site.max_deflection_mm',
                    'value': 14.665581,
                    'unit': 'mm'
                }
            ]
        },

        {
            'injury_name':
                '늑골 골절',

            'anatomical_region':
                '흉골 및 늑골 골격계 (Thoracic Skeleton)',

            'plausibility':
                'High',

            'expected_severity':
                'Moderate to High',

            'specificity':
                'mechanistic_hypothesis',

            'reasoning':
                '충격점의 고속 국소 변형 및 VC 피크와 갑옷의 국소 '
                '파손이 결합되어 흉골 및 국소 늑골 부위에 집중적인 '
                '굽힘 및 충격 하중이 전달되었을 가능성이 매우 높습니다.',

            'cannot_confirm_because':
                '서로게이트 내에 해부학적 늑골 구조(Rib Geometry) 및 '
                '뼈 재질 모델이 명시적으로 존재하지 않아 골절 여부를 '
                '직접 계산할 수 없습니다.',

            'evidence': [
                {
                    'feature_path':
                        'torso_response.impact_site.peak_vc_mps',
                    'value': 3.7689788011683243,
                    'unit': 'm/s'
                },
                {
                    'feature_path':
                        'torso_response.impact_site.max_deflection_mm',
                    'value': 14.665581,
                    'unit': 'mm'
                },
                {
                    'feature_path':
                        'armor_response.armor_local_failure_detected',
                    'value': True,
                    'unit': None
                }
            ]
        },

        {
            'injury_name':
                '폐좌상 (Lung Contusion)',

            'anatomical_region':
                '흉강 내 장기 - 폐 (Pulmonary)',

            'plausibility':
                'High',

            'expected_severity':
                'High',

            'specificity':
                'mechanistic_hypothesis',

            'reasoning':
                '약 15.75 kJ의 에너지 전달과 높은 충격점 점성 응답'
                '(VC 3.77 m/s)은 흉벽을 통해 내부 폐 실질로 고속 '
                '둔상 충격파와 변형 에너지가 전달되어 실질 출혈 및 '
                '좌상을 일으키기에 충분한 기계적 메커니즘입니다.',

            'cannot_confirm_because':
                '서로게이트 내에 폐 실질(Lung Parenchyma) 재질 및 '
                '공기-조직 계면 형상이 포함되어 있지 않아 폐 손상을 '
                '직접 관찰할 수 없습니다.',

            'evidence': [
                {
                    'feature_path':
                        'torso_response.impact_site.peak_vc_mps',
                    'value': 3.7689788011683243,
                    'unit': 'm/s'
                },
                {
                    'feature_path':
                        'projectile_response.'
                        'projectile_kinetic_energy_loss_j',
                    'value': 15752.213539089455,
                    'unit': 'J'
                }
            ]
        },

        {
            'injury_name':
                '심부 종창 및 종각 내 혈관 손상 가능성',

            'anatomical_region':
                '종각 및 심혈관계 (Mediastinum / Cardiovascular)',

            'plausibility':
                'Moderate',

            'expected_severity':
                'High to Critical',

            'specificity':
                'mechanistic_hypothesis',

            'reasoning':
                '충격 지점의 높은 속도 및 토르소 중앙부의 '
                '666.1 g(3ms average)에 달하는 급격한 충격 가속도는 '
                '흉강 내부 구조물에 강한 전단 응력과 관성 하중에 의한 '
                '자극을 가할 수 있습니다.',

            'cannot_confirm_because':
                '심장, 대혈관, 종각 등의 구조가 해부학적으로 모델링되어 '
                '있지 않으며 복잡한 내부 혈류 및 조직 전단 파손을 '
                '시뮬레이션하지 않습니다.',

            'evidence': [
                {
                    'feature_path':
                        'torso_response.torso_center_acceleration.'
                        'vector_average_3ms_peak_g',
                    'value': 666.1052319838676,
                    'unit': 'g'
                },
                {
                    'feature_path':
                        'torso_response.impact_site.peak_vc_mps',
                    'value': 3.7689788011683243,
                    'unit': 'm/s'
                }
            ]
        }
    ],

    'less_supported_injuries': [
        {
            'injury_name':
                '복부 광범위 장기 파열 (간, 비장 등)',

            'reason':
                '복부 영역 측점에서의 Peak VC는 0.16 m/s, 최대 압축률은 '
                '2.17%로 매우 제한적인 응답을 보였으므로, 충격점 위치에서 '
                '떨어진 복부 전역 장기의 광범위한 파열 가능성은 '
                '상대적으로 낮습니다.'
        },

        {
            'injury_name':
                '전역 흉부 찌그러짐에 의한 심각한 기흉/혈흉',

            'reason':
                '흉부 전역 측점(Chest Measurement)의 Peak VC는 '
                '0.17 m/s, 압축률은 2.23%로 전역적인 흉부 압축 응답은 '
                '매우 작아 전역적 변형에 의한 대규모 기흉/혈흉보다는 '
                '충격점 직하의 국소 손상 메커니즘이 우세합니다.'
        }
    ],

    'perforation_assessment': {
        'status':
            'Unknown',

        'reason':
            'armor_local_failure_detected는 true로 충격 위치 부근 요소의 '
            '파손 및 노드 삭제가 감지되었으나, '
            'armor_perforation_detected 데이터가 null이며 파손 이후 '
            '이력이 제공되지 않아 완전 관통 여부는 이 데이터만으로 '
            '확정할 수 없습니다.'
    },

    'summary':
        '본 시뮬레이션은 3.8 kg 발사체가 250 m/s로 충격하는 '
        '고에너지 조건에서 방탄재 국소 파손과 함께 충격 지점 중심의 '
        '높은 국소 응답(Peak VC 3.77 m/s, Max Deflection 14.67 mm) '
        '및 높은 충격 가속도(3ms avg 666.1 g)를 유발함을 보여줍니다. '
        '흉부 및 복부 전역 측정점의 응답은 상대적으로 낮아 하중이 '
        '충격 지점에 집중되는 양상입니다. 인체 적용 시 충격점 직하의 '
        '흉벽 심부 연조직 손상, 늑골 골절, 폐좌상 등이 강력한 부상 '
        '후보로 추론됩니다. 단, 관통 여부는 데이터상 불명확하며, '
        '사용된 모델이 균일 점탄성 서로게이트로서 해부학적 골격 및 '
        '장기가 미구현되어 있어 실제 부상 확진 및 정량적 AIS 평가에는 '
        '한계가 있으므로 종합 Confidence는 Low로 평가됩니다.',

    'model_limitations': [
        'Homogeneous viscoelastic torso surrogate; '
        'not a validated human body model.',

        'Material values are illustrative defaults '
        'and require calibration.',

        'Torso acceleration is measured at a single center node, '
        'not the torso center of mass.',

        'Dujeong armor is a homogenized equivalent panel, '
        'not discrete plates and textile.',

        'Requested projectile mass differs from the nominal spherical '
        'mass; projectile density is scaled by 1.96871.'
    ],

    'clinical_diagnosis_possible': False
}

def OutputTranslation(d):
    print('전반적인 부상 위험도: ' + d['overall_mechanical_concern'])
    print('해당하는 부상: ' + d['human_injury_severity_estimate'])
    print('모델 신뢰도: ' + d['human_injury_severity_confidence'])

    print()
    print()

    print('<시뮬레이션 결과 확인된 목록>')
    for idx, i in enumerate(d['mechanical_findings']):
        print(str(idx+1)+'. '+i['finding'])
        print('  I. 우려도: '+ i['concern_level'])
        print('  II. 시뮬레이션 근거')
        for jdx, j in enumerate(i['evidence']):
            tmp = str(jdx+1)+'. '+j['feature_path']+': '
            if j['value']!=None:
                tmp+=str(j['value'])
                if j['unit']!=None and j['unit']!='dimensionless':
                    tmp+=j['unit']
            print('    '+tmp)
        print()

    print()

    print('<예측되는 부상 목록>')
    for idx, i in enumerate(d['possible_injuries']):
        print(str(idx+1)+'. '+i['injury_name'])
        print('  I. 해부학적 위치: '+i['anatomical_region'])
        print('  II. 가능성: '+i['plausibility'])
        print('  III. 심각성: '+i['expected_severity'])
        print('  IV. 특이성: '+i['specificity'])
        print('  V. 사유: '+i['reasoning'])
        print('  VI. 한계: '+i['cannot_confirm_because'])
        print('  VII. 시뮬레이션 근거')
        for jdx, j in enumerate(i['evidence']):
            tmp = str(jdx+1)+'. '+j['feature_path']+': '
            if j['value']!=None:
                tmp+=str(j['value'])
                if j['unit']!=None and j['unit']!='dimensionless':
                    tmp+=j['unit']
            print('    '+tmp)

        print()

    print()

    print('<가능성은 있으나 확실하지 않은 부상 목록>')
    for idx, i in enumerate(d['less_supported_injuries']):
        print(str(idx+1)+'. '+i['injury_name'])
        print('  사유: '+i['reason'])
        print()

    print()

    print('<갑옷>')
    print('갑옷 상태: '+d['perforation_assessment']['status'])
    print('사유: '+d['perforation_assessment']['reason'])

    print()
    print()

    print('<내용 요약>')
    print(d['summary'])