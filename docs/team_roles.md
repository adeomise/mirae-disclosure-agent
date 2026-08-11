# 이번 주 팀 역할

이번 주 역할은 기능 모듈의 개인 소유권이 아니라, 구현 전에 필요한 분석·설계 산출물에 따라
구분합니다.

## 팀원 1 — 공시 데이터·XML 구조 분석

- manifest, universe, XML 사이의 관계 분석
- 대표 XML 3~5개의 구조 비교
- 정기공시와 거래소공시의 구조 및 특성 차이 분석
- Section, Table, 단위, 행·열, 당기·전기 값의 위치 확인
- 원본과 정정공시를 구분하고 연결하는 방법 조사
- 원문 값·단위·계층 구조를 보존하는 Parser 출력 예시 작성

주요 문서 위치는 `docs/data_analysis/`입니다. 이번 주 목표는 분석 결과와 Parser 출력 예시를
제시하는 것이며, 범용 Parser를 구현하는 것이 아닙니다.

## 팀원 2 — 질문 유형·검색·Agent 동작 설계

- 지원할 질문 범위 정의
- 대표 질문 15개 작성
- 질문 유형 분류
- 질문별 필요한 문서와 정보 정의
- 예상 검색 과정 설계
- 이상적인 답변 형식 설계
- 답변 가능한 범위와 답변 불가능한 범위 정의

주요 문서 위치는 `docs/agent_design/`입니다. 이번 주 목표는 질문에서 검색과 답변까지의
동작을 설계하는 것이며, 실제 Retrieval이나 LLM을 구현하는 것이 아닙니다.

## 팀원 3 — 평가 데이터셋·아키텍처·프로젝트 관리

- Gold Question, Answer, Document 구조 설계
- 검색 및 답변 평가 기준 정의
- 전체 Agent 아키텍처 설계
- MVP 범위 정의
- 개발 일정 관리
- GitHub 구조 관리
- 공통 문서 통합

주요 문서 위치는 `docs/evaluation/`과 공통 프로젝트 문서입니다. 이번 주에는
API, HyperCLOVA X, 검색 구현이 주 업무가 아닙니다.

## 공용 모듈과 소유권

`src/disclosure_agent/periodic`, `src/disclosure_agent/event`,
`src/disclosure_agent/retrieval`, `src/disclosure_agent/agent`,
`src/disclosure_agent/api`는 향후 시스템 구현을 위한 공용 모듈 구조입니다. 특정 팀원의
소유 폴더가 아니며, 이번 주 역할을 이 폴더들에 대응시켜 해석하지 않습니다. 향후 구현 작업은
분석·설계 결과와 팀 합의에 따라 별도로 배정합니다.

## 공동 작업

Data Contract는 현재 Draft입니다. 팀원 1의 XML 분석, 팀원 2의 질문·Agent 설계, 팀원 3의
평가 요구사항을 반영한 뒤 전원이 검토하여 확정합니다. MVP 범위, 아키텍처 경계, 공통 Schema
변경도 Issue 또는 설계 논의를 거쳐 전원 합의 후 반영합니다.
