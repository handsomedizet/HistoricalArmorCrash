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