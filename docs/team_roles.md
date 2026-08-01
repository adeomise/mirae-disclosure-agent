# 팀 역할

## 담당자 1 — 정기공시

정기공시 XML 구조 분석, 사업·반기·분기보고서 Parser, 재무제표와 주석 추출, 정기공시
Chunk 생성을 담당합니다. 주 작업 위치는 `src/disclosure_agent/periodic`입니다.

## 담당자 2 — 이벤트공시

이벤트공시 XML 구조 분석, 공급계약·신규시설투자 Parser, 원본과 정정공시 연결, 변경사항
비교를 담당합니다. 주 작업 위치는 `src/disclosure_agent/event`입니다.

## 담당자 3 — 공통 런타임

공통 Schema, 검색·Agent Runtime, HyperCLOVA X 연동, FastAPI, 평가 자동화를 담당합니다. 주
작업 위치는 `common`, `retrieval`, `agent`, `api`, `evaluation`입니다.

## 공동 작업

Pilot XML 선정, 공통 데이터 계약 검토, 통합 테스트, 오류 분류와 MVP 승인 기준은 전원이
공동으로 결정합니다. 공통 Schema 변경은 Issue 또는 설계 논의를 거쳐 전원 합의 후 반영합니다.
