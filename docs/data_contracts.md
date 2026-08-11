# 공통 데이터 계약 Draft

이 문서는 구현 코드나 확정 Schema가 아닌 팀 간 협의를 위한 JSON 형식 **Draft**입니다.
현재 필드, 계층, 필수 여부를 구현 계약으로 간주하지 않습니다. 다음 세 가지 산출물을 함께
반영하고 전원 합의를 거친 뒤 확정합니다.

- 공시 데이터·XML 분석: manifest/universe/XML 관계, 원문 Section/Table/단위/행열,
  당기·전기 위치, 정기·거래소공시 차이, 정정공시 구분 방식
- 질문·Agent 설계: 질문 유형별 필요한 문서와 정보, 예상 검색 과정, 답변 형식,
  답변 가능·불가능 범위
- 평가 요구사항: Gold Question/Answer/Document 구조, 검색 평가 기준, 답변 평가 기준과
  Evidence 연결 방식

Draft를 수정할 때에는 Parser가 원문의 값, 단위, 계층 구조를 보존할 수 있는지와 평가 가능한
근거 식별자를 제공하는지를 함께 검토합니다. 실제 기업명·수치·접수번호는 사용하지 않습니다.

## 1. DocumentMetadata

문서 식별과 필터링에 필요한 공통 메타데이터입니다.

```json
{"doc_id":"periodic_SAMPLE_ID","corp_name":"테스트기업","report_name":"분기보고서","receipt_date":"YYYY-MM-DD","document_type":"periodic","source_path":"raw/SAMPLE.xml"}
```

## 2. ParsedDocument

원문 구조를 보존해 파싱한 한 문서의 최상위 표현입니다.

```json
{"metadata":{"doc_id":"periodic_SAMPLE_ID"},"sections":[{"section_id":"section_SAMPLE_01"}],"tables":[{"table_id":"table_SAMPLE_01"}]}
```

## 3. ParsedSection

제목과 계층, 원문 텍스트 범위를 나타냅니다.

```json
{"section_id":"section_SAMPLE_01","title":"사업의 내용","level":1,"parent_section_id":null,"text":"SAMPLE_TEXT"}
```

## 4. ParsedTable

표의 제목, 단위, 행·열과 원문 위치를 보존합니다.

```json
{"table_id":"table_SAMPLE_01","title":"SAMPLE_TABLE","unit":"SAMPLE_UNIT","columns":["항목","당기"],"rows":[["SAMPLE_ITEM","SAMPLE_VALUE"]],"section_id":"section_SAMPLE_01"}
```

## 5. Chunk

검색 대상으로 사용할 문서 조각과 출처 메타데이터입니다.

```json
{"chunk_id":"chunk_SAMPLE_01","doc_id":"periodic_SAMPLE_ID","section_id":"section_SAMPLE_01","text":"SAMPLE_TEXT","metadata":{"report_name":"분기보고서"}}
```

## 6. Fact

원문에서 추출한 값과 단위, 기간, 근거 위치입니다.

```json
{"fact_id":"fact_SAMPLE_01","name":"SAMPLE_METRIC","value":"SAMPLE_VALUE","unit":"SAMPLE_UNIT","period":"YYYY-QN","evidence_ref":"section_SAMPLE_01"}
```

## 7. Event

이벤트공시의 유형과 핵심 필드, 문서 연결 정보입니다.

```json
{"event_id":"event_SAMPLE_01","event_type":"supply_contract","doc_id":"event_SAMPLE_ID","fields":{"counterparty":"SAMPLE_COUNTERPARTY"},"amends_event_id":null}
```

## 8. EventChange

원본과 정정공시 사이의 필드 단위 변경을 나타냅니다.

```json
{"change_id":"change_SAMPLE_01","event_id":"event_SAMPLE_01","field":"contract_period","before":"SAMPLE_BEFORE","after":"SAMPLE_AFTER","evidence_refs":["section_SAMPLE_01","section_SAMPLE_02"]}
```

## 9. RetrievedChunk

검색된 Chunk와 검색 점수·방식을 전달합니다.

```json
{"chunk":{"chunk_id":"chunk_SAMPLE_01"},"score":0.0,"retrieval_method":"keyword","rank":1}
```

## 10. CalculationResult

Python 계산 결과와 입력 Fact, 식별 가능한 계산식을 기록합니다.

```json
{"calculation_id":"calc_SAMPLE_01","operation":"ratio","inputs":["fact_SAMPLE_01","fact_SAMPLE_02"],"value":"SAMPLE_RESULT","unit":"percent","formula":"A / B * 100"}
```

## 11. Evidence

답변 주장과 원문 근거를 연결합니다.

```json
{"evidence_id":"evidence_SAMPLE_01","doc_id":"periodic_SAMPLE_ID","section_id":"section_SAMPLE_01","chunk_id":"chunk_SAMPLE_01","quote":"SAMPLE_EXCERPT"}
```

## 12. AnswerRequest

사용자 질문과 선택적 검색 조건입니다.

```json
{"request_id":"request_SAMPLE_01","question":"SAMPLE_QUESTION","filters":{"corp_name":"테스트기업","period":"YYYY-QN"}}
```

## 13. AnswerResponse

최종 답변, 근거, 계산 결과와 안전성 경고를 묶습니다.

```json
{"request_id":"request_SAMPLE_01","answer":"SAMPLE_ANSWER","evidence":[{"evidence_id":"evidence_SAMPLE_01"}],"calculations":[],"warnings":[]}
```
