"""Agent Pattern handler 모듈.

역할:
- docs/agent_design/agent_patterns.md의 실행 Pattern 하나를 파일 하나가 담당한다.

현재 구현 범위:
- Pattern A (Single Fact Lookup)
- Pattern B (Section Summary) — 정기공시 단건 요약(Q04) 경로. 기간을 밝히지 않은 요약(Q05)과
  거래소공시 단건 요약(Q06)은 각각 D5-a·D5가 미정이라 아직 열려 있지 않다.
- Pattern C (Temporal Compare)
- Pattern D (Cross-entity Compare) — 원천 지표(Q10)·파생지표(Q11) 경로.
  거래소공시 집계 경로(Q12)는 아직 없다.
- Pattern E (Event History)

Pattern을 추가할 때:
- 이 디렉터리에 모듈을 하나 더 만들고 agent/router.py의 HANDLERS에 등록한다.
- handler는 (QueryPlan, corpus_root, manifest_path)를 받아 직렬화 가능한 결과 dict를
  돌려주는 형태를 지킨다. 예외를 올리지 않고 막힌 지점을 blocked 결과로 표현한다.
- 한 intent 안에서 일부 계획만 다룰 수 있으면 그 판정을 handler 입구에서 한다. Router는
  intent만 보므로 그 자리가 아니다(handlers/temporal_compare.py 참조).

TODO:
- Pattern B의 Q05·Q06 경로. 기본 문서 선택 정책(D5-a)과 판본 선택 정책(D5)이 정해진 뒤에 연다.
"""
