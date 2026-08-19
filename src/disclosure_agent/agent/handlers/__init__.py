"""Agent Pattern handler 모듈.

역할:
- docs/agent_design/agent_patterns.md의 실행 Pattern 하나를 파일 하나가 담당한다.

현재 구현 범위:
- Pattern A (Single Fact Lookup) 하나만 구현되어 있다.

Pattern을 추가할 때:
- 이 디렉터리에 모듈을 하나 더 만들고 agent/router.py의 HANDLERS에 등록한다.
- handler는 (QueryPlan, corpus_root, manifest_path)를 받아 직렬화 가능한 결과 dict를
  돌려주는 형태를 지킨다. 예외를 올리지 않고 막힌 지점을 blocked 결과로 표현한다.

TODO:
- Pattern C (temporal_compare)
- Pattern E (event_history)
"""
