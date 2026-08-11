# DART 정기공시 XML 구조 분석

## 분석 목적

정기공시 XML Parser 구현에 앞서 DART XML의 문서 계층,
본문, 표, 메타데이터 구조를 파악하고 파싱 규칙의 초안을 정의한다.

> 주의: 현재 내용은 삼성전자 2023 사업보고서
> `20240312000736.xml`을 Pilot 문서로 분석한 결과이다.
> 다른 기업 및 다른 보고서에서도 동일한 구조인지 추가 검증이 필요하다.

## Pilot 문서

- 회사: 삼성전자주식회사
- 보고서 유형: 사업보고서
- 사업연도: 2023
- 제출일: 2024-03-12
- 파일: `20240312000736.xml`

## 1. 문서 메타정보

- DOCUMENT: XML 전체 루트
- DOCUMENT-NAME: 사업보고서
- COMPANY-NAME: 삼성전자주식회사
- BODY: 실제 공시 본문

## 2. Section 계층

- SECTION-1: 대분류/장
  - 예: II. 사업의 내용

- SECTION-2: 절
  - 예: 1. 사업의 개요

- SECTION-3: 하위 절
  - 예: 7-1. 증권의 발행을 통한 자금조달 실적

- TITLE: 각 SECTION의 제목
  - 단, TABLE-GROUP 안에도 TITLE이 존재하므로 부모 태그 확인 필요

## 3. 본문

- P: 일반 문단
- SPAN: 문장 내부 강조/서식
- SPAN text는 부모 P text와 합쳐야 함
- 가./나./(1) 등의 소제목은 별도 SECTION이 아니라 P/SPAN으로 표현되기도 함

## 4. 표

- TABLE-GROUP: 연관된 여러 TABLE 묶음
- TABLE: 표
- THEAD: 표 header
- TBODY: 표 body
- TR: row
- TH: header cell
- TD: 일반 cell
- TE: 코드가 붙은 데이터 cell
- TU: 단위/날짜 정보가 붙은 cell

## 5. 중요한 Attribute

- ATOC: 목차 포함 여부
- ATOCID: 제목 ID
- AASSOCNOTE: 내부 항목 관련 정보
- ACODE: 구조화된 데이터 항목 코드
- AUNIT: 단위/의미
- AUNITVALUE: 구조화된 실제 값
- ACLASS: 표/구조 유형
- REFNO: 문서 내부 참조

## 6. 기타

- LIBRARY: wrapper이며 내부 content를 버리면 안 됨
- PGBRK: page break → 기본적으로 제거
- IMAGE/IMG: 이미지
- IMG-CAPTION: 이미지 설명
- A: 내부 링크

## 7. Parser 설계 시 주의점

1. 모든 TITLE을 section title로 간주하면 안 됨
2. SECTION-n의 n을 hierarchy depth로 사용
3. SPAN은 P에 병합
4. TABLE에서 TD만 읽으면 안 되고 TH/TD/TE/TU 모두 처리
5. TABLE-GROUP의 단위/기준일과 실제 데이터 TABLE 연결 필요
6. ACODE/AUNIT/AUNITVALUE 등의 구조화 정보는 보존
7. 소제목은 P/SPAN 내부에 존재할 수 있음