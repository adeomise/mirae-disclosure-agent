"""최종 답변 조립 모듈.

역할:
- 검증된 사실, 계산 결과, Evidence와 경고를 AnswerResponse로 조립한다.

예정 입력:
- AnswerRequest
- 검증된 Fact, CalculationResult, Evidence와 모델 출력

예정 출력:
- AnswerResponse

TODO:
- 답변 형식과 근거 표시 규칙 구현
- 근거 부족 및 부분 응답 정책 구현
- Chain-of-Thought가 응답에 포함되지 않는지 검증
"""
