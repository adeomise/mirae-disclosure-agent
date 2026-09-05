"""모델 응답 검증 모듈.

역할:
- 근거 payload를 외부 모델에 넘기고, 돌아온 문장이 근거 밖으로 나가지 않았는지 확인한다.

공개 경계:
    GroundedPayload -> verbalize -> str | None

    None은 "표현을 바꾸지 않는다"는 뜻이다. 오류가 아니다. 모델이 죽었거나, 응답을 읽지
    못했거나, 검증을 통과하지 못한 경우 모두 같은 값으로 답한다. 호출부의 대응이 셋 다
    같기 때문이다: 결정론 문장을 그대로 쓴다.

    그래서 이 모듈은 예외를 밖으로 내보내지 않는다. 실패를 예외로 올리면 호출부가 "모델이
    없다"와 "요청이 실패했다"를 다시 구분해야 하고, 한 번 빠뜨리면 모델 장애가 사용자에게
    500으로 나간다.

거절한 응답을 다시 묻지 않는다:
    같은 질문이 실행마다 다른 문장으로 답해지면 평가가 대조할 것이 없어진다. hyperclova의
    재시도는 "모델이 응답하지 못했다"에 대한 것이고, 여기는 "모델이 응답했는데 근거 밖으로
    나갔다"이다. 둘은 다른 실패이며 대응도 다르다. 뒤쪽은 다시 물어도 같은 종류로 틀릴 수
    있고, 우연히 통과한 응답을 채택하면 검증이 표본 추출이 된다.

검증의 방향:
    모델에게 무엇을 근거로 썼는지 신고하게 하지만(used_*_ids), 그 신고를 믿고 통과시키지는
    않는다. 신고는 payload 안의 id인지 확인하는 데만 쓴다. 진짜 방어선은 문장에 실제로
    나타난 수치·기업·회계기준을 payload와 대조하는 쪽이다. 모델이 정직하게 신고한다는 보장이
    없으므로, 통과 여부를 신고에 맡기면 검증이 모델의 협조에 기대게 된다.

    빠뜨림도 함께 본다. 틀린 숫자를 쓰는 것보다 잡기 어렵다. 형식은 완전한 답변이고, 무엇이
    사라졌는지는 문장 어디에도 남지 않는다.

로깅:
    응답 본문을 남기지 않는다. 본문에는 확보한 공시 수치가 들어 있다. 남기는 것은 어떤
    규칙에 걸렸는가(AnswerRejectedError.code)뿐이다.

입력:
- GroundedPayload, ChatCompletionClient

출력:
- 검증을 통과한 한국어 문장, 또는 None

TODO:
- DocumentSummary(Pattern B) 응답 검증. 현재는 payload 단계에서 먼저 막힌다.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from disclosure_agent.agent.answer_builder import ACCOUNTING_SCOPE_LABELS
from disclosure_agent.agent.grounded_payload import GroundedComparison, GroundedPayload
from disclosure_agent.agent.hyperclova import ChatCompletionClient, HyperClovaError
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ComparisonRelation,
    ExecutionStatus,
    MetricConcept,
)

logger = logging.getLogger(__name__)

# 답변 길이 상한.
#
# 확정된 사실 몇 건을 옮기는 일이라 길어질 이유가 없다. 길어졌다면 payload에 없는 말이
# 섞였을 가능성이 높고, 그때 어느 문장이 근거 있는 것인지 사용자가 가릴 수 없다.
MAX_ANSWER_LENGTH = 600

# 문장에서 수치를 찾는 규칙. 천단위 구분과 소수점을 포함한다.
NUMBER_PATTERN = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 회계 기준 표기. payload가 말하지 않은 쪽이 문장에 나오면 연결·별도가 뒤바뀐 답이 된다.
#
# 목록을 손으로 적지 않고 결정론 대응표에서 만든다. 두 벌이 되면 새 기준이 추가됐을 때
# 이 검사만 따라가지 못하고, 그 값은 조용히 통과한다.
ACCOUNTING_SCOPE_WORDS: frozenset[str] = frozenset(ACCOUNTING_SCOPE_LABELS.values())

# 투자 판단·예측으로 읽히는 표현.
#
# 판정 자체는 safety.Gate가 질문 단계에서 막는다. 여기는 사실 조회로 통과한 질문의 답변에
# 그 성격의 문장이 덧붙지 않는지 보는 두 번째 자리다(pattern_f_safety.md §8 원칙 4).
ADVISORY_EXPRESSIONS: tuple[str, ...] = (
    "매수",
    "매도",
    "추천",
    "유망",
    "투자하",
    "사도 ",
    "팔아도 ",
    "전망됩니다",
    "예상됩니다",
    "예측됩니다",
    "될 것으로 보입니다",
)

# 우열을 말하는 표현과 그 방향.
#
# 목록을 짧게 둔다. 여기에 없는 표현으로 우열을 말한 답변은 "판정을 확인할 수 없음"으로 걸러져
# 결정론 문장으로 돌아간다. 넓히다가 하나라도 방향을 잘못 적으면 뒤집힌 판정이 통과하므로,
# 확실한 것만 담고 나머지는 fallback에 맡긴다.
GREATER_EXPRESSIONS: tuple[str, ...] = (
    "더 큽니다",
    "더 많습니다",
    "더 높습니다",
    "더 큰",
    "더 많은",
    "상회",
    "웃돕니다",
)

SMALLER_EXPRESSIONS: tuple[str, ...] = (
    "더 작습니다",
    "더 적습니다",
    "더 낮습니다",
    "더 작은",
    "더 적은",
    "하회",
    "밑돕니다",
)

# 견준 두 '값'이 같다고 말하는 표현.
#
# 같다고 말하는 대상이 무엇인지까지 본다. 동등의 주어가 값이 아니면 판정을 옮긴 문장이
# 아니기 때문이다. 실제로 걸러야 하는 것이 그 자리다:
#
#     "두 기업은 동일 업종입니다"              업종이 같다는 말이다
#     "두 회사의 연결 기준에는 차이가 없습니다"  회계 기준이 같다는 말이다
#
# 둘 다 견준 수치에 대해서는 아무 말도 하지 않는다. 이런 문장을 동등 진술로 세면 판정이
# 빠진 답변이 통과하고, 값을 견주는 일은 사용자에게 남는다.
#
# 그래서 수량사(두/양쪽/둘)와 값 명사(값/수치/금액)가 붙어 있는 꼴만 인정한다. '기업'과
# '회사'를 값 명사에서 뺐고, 대상 없는 "차이가 없습니다"도 뺐다.
#
# 서술어도 끝까지 본다. 어간(같/동일)에서 끊으면 관형형이 함께 걸리기 때문이다:
#
#     "두 값은 같은 기준으로 계산되었습니다"    계산 기준이 같다는 말이다
#     "두 금액은 동일한 단위로 표시되었습니다"  표시 단위가 같다는 말이다
#
# 둘 다 뒤에 오는 명사를 꾸미는 자리이고 값이 같다고 말하지 않는다. 그래서 종결형만 받는다.
#
# 여기에 없는 표현으로 동등을 말한 답변은 판정을 확인할 수 없는 것으로 보고 결정론 문장으로
# 돌아간다.
EQUALITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(두|양쪽|둘)\s*(의)?\s*(값|수치|금액)[은는이가]?\s*(서로\s*)?(같습니다|동일합니다)"
    ),
)

# 비교 대상을 뒤집어 말하는 조사.
#
# "A가 B보다 큽니다"는 우열 표현 바로 앞에 진 쪽이 온다. 앞에서부터 이름을 찾는 규칙으로는
# 방향을 반대로 읽게 되므로, 이 조사가 낀 절은 판정하지 않고 거절한다. 좌우를 조용히 다시
# 해석하지 않는다.
INVERTING_PARTICLE = "보다"

# 절의 경계. 우열 표현이 어느 이름에 걸리는지 볼 범위를 한 문장으로 자른다.
CLAUSE_BOUNDARIES = ".!?\n"

# 변경이 없었다는 진술.
#
# answer_builder._event_history_sentence가 실제로 쓰는 두 표현("확인된 변경 항목은 없습니다",
# "이후 변경된 내용은 확인되지 않았습니다")을 포함한다. 결정론 문장이 자기 검증기를 통과하지
# 못하면 대조군이 사라진다.
NO_CHANGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"변경(된)?\s*(내용|사항|항목)?[은는이가]?\s*(없|확인되지\s*않|확인하지\s*못)"),
    re.compile(r"바뀐\s*(내용|것|사항)?[은는이가]?\s*없"),
    re.compile(r"정정(된)?\s*(내용|사항)?[은는이가]?\s*없"),
)

# 변경이 있었다는 진술.
NO_CHANGE_CONTRADICTIONS: tuple[re.Pattern[str], ...] = (
    re.compile(r"변경\s*(된)?\s*(내용|사항|항목)?[은는이가]?\s*\d+\s*건"),
    re.compile(r"\d+\s*건[이은]?\s*(변경|정정)"),
    re.compile(r"(변경|정정)되었|(변경|정정)됐|바뀌었"),
)

# 이력을 끝까지 확인했다는 진술. chain_complete=False인 결과에서 거절한다.
CHAIN_COMPLETE_CLAIMS: tuple[re.Pattern[str], ...] = (
    re.compile(r"완결"),
    re.compile(r"(모두|전부|끝까지)\s*확인"),
    re.compile(r"(전체|모든)\s*이력[을은는이가]?\s*확인했"),
    re.compile(r"최종\s*(이력|상태|내용)"),
)

# 이력이 끊겼다는 진술. chain_complete=True인 결과에서 거절한다.
#
# "확인하지 못했습니다"만으로 판정하지 않는다. 체인은 끝까지 확보했는데 원문에서 일부 field가
# 유보된 결과(WITHHELD_IN_SOURCE)의 한계 문장이 그 표현을 쓰고, 그 문장은 답변에 반드시 남아야
# 한다(_require_limitation_when_partial). 여기서는 이력 자체가 미완결이라는 진술만 본다.
CHAIN_INCOMPLETE_CLAIMS: tuple[re.Pattern[str], ...] = (
    re.compile(r"불완전"),
    re.compile(r"완결되지\s*(않|못)"),
    re.compile(r"이력[을은는이가]?\s*(일부|중간)"),
    re.compile(r"(전체|모든)\s*이력[을은는이가]?\s*확인하지\s*못"),
)

SYSTEM_PROMPT = """당신은 한국 공시 데이터 질의응답 시스템의 '표현' 단계입니다.

당신의 유일한 임무는 아래 JSON에 이미 확정되어 있는 사실을 한국어 문장으로 옮기는 것입니다.
당신은 판단하지 않고, 계산하지 않고, 조회하지 않습니다.

반드시 지킬 것:
1. JSON에 없는 숫자를 쓰지 마십시오. 반올림·단위 변환·자릿수 변경도 금지입니다.
   숫자와 단위는 value 필드에 적힌 문자열 그대로 옮기십시오.
2. JSON에 없는 회사명·연도·회계기준(연결/별도)·지표명을 쓰지 마십시오.
3. facts와 calculations에 있는 값은 하나도 빠뜨리지 말고 모두 문장에 담으십시오.
4. comparisons의 relation은 이미 확정된 판정입니다. 값을 다시 비교하지 말고 그 판정을
   그대로 옮기십시오. left_greater는 left_id가 큰 것, right_greater는 right_id가 큰 것입니다.
5. limitation이 있으면 그 문장을 그대로 마지막에 붙이십시오.
6. 투자 판단, 매수·매도 의견, 미래 예측, 전망을 쓰지 마십시오.
7. fact_id, evidence_id, calculation_id 같은 식별자를 문장에 쓰지 마십시오.
8. 영어 식별자(revenue, operating_income, ANSWERABLE 등)를 문장에 쓰지 마십시오.
9. JSON에 없는 배경 설명, 해석, 추측을 덧붙이지 마십시오.

출력 형식 - 아래 JSON만 출력하십시오. 코드 펜스나 다른 설명을 붙이지 마십시오.
{
  "answer": "한국어 답변 문장",
  "used_fact_ids": ["사용한 fact_id"],
  "used_calculation_ids": ["사용한 calculation_id"],
  "used_evidence_ids": ["사용한 evidence_id"]
}
"""


class AnswerRejectedError(RuntimeError):
    """모델 응답을 쓸 수 없다고 판정한 경우.

    code는 로그에 남길 짧은 이름이다. 응답 본문을 로그에 남기지 않으므로, 무엇 때문에
    걸렸는지는 이 값으로만 남는다.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")

        self.code = code


@dataclass(frozen=True)
class ModelAnswer:
    """모델이 신고한 응답.

    used_*_ids는 보조 신호다. 모델이 정직하게 신고한다는 보장이 없으므로, 화이트리스트
    대조에는 쓰되 이것만으로 통과시키지 않는다.
    """

    answer: str
    used_fact_ids: tuple[str, ...]
    used_calculation_ids: tuple[str, ...]
    used_evidence_ids: tuple[str, ...]


def verbalize(payload: GroundedPayload, *, client: ChatCompletionClient) -> str | None:
    """
    근거 payload를 모델 문장으로 옮긴다. 쓸 수 없으면 None.

    예외를 밖으로 내보내지 않는다. 외부 모델의 실패는 이 시스템의 실패가 아니며, 그 경우의
    동작은 이미 정해져 있다(결정론 문장 사용).
    """
    try:
        raw = client.complete(system_prompt=SYSTEM_PROMPT, user_message=payload.to_json())

    except HyperClovaError as error:
        logger.warning("HyperCLOVA X verbalization unavailable: %s", type(error).__name__)

        return None

    try:
        answer = parse_model_answer(raw)

        validate_answer(answer, payload)

    except AnswerRejectedError as error:
        # 본문은 남기지 않는다. 어떤 규칙에 걸렸는지만 남긴다.
        logger.warning("HyperCLOVA X answer rejected: %s", error.code)

        return None

    return answer.answer


def parse_model_answer(raw: str) -> ModelAnswer:
    """
    구조화 응답을 읽는다.

    자유 문자열을 받지 않는 이유는 모델이 무엇을 근거로 썼다고 신고하는지를 대조할 자리가
    필요하기 때문이다. 형식이 어긋나면 문장만 건져 쓰지 않는다. 형식을 지키지 못한 응답은
    내용도 지시를 따랐다고 볼 근거가 없다.
    """
    body = _strip_code_fence(raw.strip())

    try:
        decoded = json.loads(body)

    except ValueError as error:
        raise AnswerRejectedError("not_json", "응답이 JSON이 아닙니다") from error

    if not isinstance(decoded, dict):
        raise AnswerRejectedError("not_object", "응답 최상위가 객체가 아닙니다")

    answer = decoded.get("answer")

    if not isinstance(answer, str) or not answer.strip():
        raise AnswerRejectedError("empty_answer", "answer 필드가 비어 있습니다")

    return ModelAnswer(
        answer=answer.strip(),
        used_fact_ids=_string_tuple(decoded.get("used_fact_ids")),
        used_calculation_ids=_string_tuple(decoded.get("used_calculation_ids")),
        used_evidence_ids=_string_tuple(decoded.get("used_evidence_ids")),
    )


def validate_answer(answer: ModelAnswer, payload: GroundedPayload) -> None:
    """
    문장이 payload 밖으로 나가지 않았는지 확인한다.

    Raises:
        AnswerRejectedError: 어느 불변식이든 어긴 경우.
    """
    _require_bounded_length(answer.answer)
    _require_declared_ids_exist(answer, payload)
    _require_no_identifier_leak(answer.answer, payload)
    _require_no_english_vocabulary(answer.answer)
    _require_grounded_numbers(answer.answer, payload)
    _require_every_value_is_stated(answer.answer, payload)
    _require_known_entities(answer.answer, payload)
    _require_consistent_accounting_scope(answer.answer, payload)
    _require_limitation_when_partial(answer.answer, payload)
    _require_preserved_relation(answer.answer, payload)
    _require_consistent_history_semantics(answer.answer, payload)
    _require_no_advisory_wording(answer.answer)


# --------------------------------------------------------------------------
# 불변식
# --------------------------------------------------------------------------


def _require_bounded_length(text: str) -> None:
    if len(text) > MAX_ANSWER_LENGTH:
        raise AnswerRejectedError(
            "too_long", f"{len(text)}자로 상한({MAX_ANSWER_LENGTH})을 넘었습니다"
        )


def _require_declared_ids_exist(answer: ModelAnswer, payload: GroundedPayload) -> None:
    """모델이 신고한 근거가 payload 안의 것인지 본다. 없는 evidence_id를 만들면 여기서 걸린다."""
    for label, declared, known in (
        ("fact", answer.used_fact_ids, {fact.fact_id for fact in payload.facts}),
        (
            "calculation",
            answer.used_calculation_ids,
            {calculation.calculation_id for calculation in payload.calculations},
        ),
        (
            "evidence",
            answer.used_evidence_ids,
            {item.evidence_id for item in payload.evidence},
        ),
    ):
        unknown = sorted(set(declared) - known)

        if unknown:
            raise AnswerRejectedError(
                f"unknown_{label}_id", f"payload에 없는 {label} id: {unknown}"
            )


def _require_no_identifier_leak(text: str, payload: GroundedPayload) -> None:
    """내부 식별자가 사용자 문장에 실리지 않는다."""
    identifiers = (
        [fact.fact_id for fact in payload.facts]
        + [calculation.calculation_id for calculation in payload.calculations]
        + [comparison.comparison_id for comparison in payload.comparisons]
        + [history.event_id for history in payload.event_histories]
        + [item.evidence_id for item in payload.evidence]
        + [item.doc_id for item in payload.evidence]
    )

    leaked = sorted({identifier for identifier in identifiers if identifier in text})

    if leaked:
        raise AnswerRejectedError("identifier_leak", f"문장에 내부 식별자가 있습니다: {leaked}")


def _require_no_english_vocabulary(text: str) -> None:
    """
    Contract enum 값이 한국어 문장에 섞이지 않는다.

    목록을 손으로 적지 않고 enum에서 만든다. 어휘가 늘 때 이 검사가 따라가지 못하면, 새 값만
    조용히 통과한다.
    """
    lowered = text.casefold()

    vocabulary = (
        [item.value for item in MetricConcept]
        + [item.value for item in ComparisonRelation]
        + [item.value for item in AnswerabilityStatus]
        + [item.value for item in AnswerabilityReason]
        + [item.value for item in ExecutionStatus]
    )

    leaked = sorted({word for word in vocabulary if word.casefold() in lowered})

    if leaked:
        raise AnswerRejectedError("english_vocabulary", f"문장에 내부 어휘가 있습니다: {leaked}")


def _require_grounded_numbers(text: str, payload: GroundedPayload) -> None:
    """
    문장의 모든 수치가 payload에서 왔는지 본다.

    이것이 가장 중요한 방어선이다. 모델이 새 숫자를 만들면 그 숫자는 공시에 없는 값이고,
    형식상 정상인 문장에 실려 나간다. 반올림도 새 숫자로 본다. 저장된 값이 우열 판정의
    근거이므로 자릿수가 달라지면 답변에 실린 수치로 검산했을 때 관계가 뒤집혀 보일 수 있다
    (answer_builder._calculation_value 참조).
    """
    allowed = _allowed_numbers(payload)

    used = {_canonical_number(match) for match in NUMBER_PATTERN.findall(text)}

    ungrounded = sorted(used - allowed)

    if ungrounded:
        raise AnswerRejectedError(
            "ungrounded_number", f"payload에 없는 수치가 있습니다: {ungrounded}"
        )


def _require_every_value_is_stated(text: str, payload: GroundedPayload) -> None:
    """
    답에 실려야 하는 값이 문장에서 빠지지 않았는지 본다.

    빠뜨리는 쪽은 틀린 숫자를 쓰는 것보다 잡기 어렵다. 형식은 완전한 답변이고, 무엇이
    사라졌는지는 문장 어디에도 남지 않는다. 여러 값을 물은 질문에 한 값만 답하는 실패
    모드이며, answer_builder._single_fact_sentence가 다중 Fact를 거부하는 이유와 같다.

    '전부'가 아니라 '답에 실리는 값'을 보는 이유:
        payload에 실린 Fact가 모두 답의 일부인 것은 아니다. 파생값을 만든 원천 Fact는 그
        파생값이 무엇에서 나왔는지를 붙들어 두려고 함께 실린다. 계산의 입력을 id로만 가리키는
        구조가 성립하려면 그 Fact가 payload 안에 있어야 하고(grounded_payload._ground_calculation),
        근거 문서로 되짚는 경로도 그 Fact를 지난다.

        그것을 문장에까지 요구하면 결정론 경로가 자기 검증기에 걸린다. 실제로 걸렸다.
        build_answer_response가 만든 Q08(기간별 영업이익률 변화)과 Q11(기업 간 영업이익률 비교)의
        문장은 원천 Fact의 값을 적지 않으며, 그 문장을 이 함수에 넣으면 value_dropped가 된다.
        두 문항은 비율을 물었고 답은 비율이다. 분자와 분모를 함께 적어야 답이 되는 것이 아니다.

        그래서 완결성의 기준을 payload의 적재 여부가 아니라 참조 위상에서 읽는다. 어떤 계산의
        입력으로 쓰인 Fact는 그 계산이 대신 답하고 있으므로 보조 값으로 본다. 그 계산 자체는
        아래에서 빠짐없이 요구하므로, 보조로 내려간 Fact가 대변되지 않은 채 사라지는 일은 없다.

    계산 결과는 깊이와 무관하게 전부 요구한다:
        다른 계산의 입력으로 쓰인 계산도 마찬가지다(Q08의 기간별 비율 2건). 계산 결과는 원문에
        적힌 값이 아니라 이 시스템이 만들어 실은 진술이며, 빠지면 그것을 대신할 것이 없다.
        원천 Fact가 파생값에 대변되는 것과 다른 자리다.

    우열 판정의 피연산자는 보조로 내리지 않는다:
        GroundedComparison.left_id/right_id는 아래 집합에 넣지 않는다. 넣으면 Q10처럼 Fact 2건을
        견준 결과에서 두 값이 모두 보조가 되어, 값을 하나도 적지 않고 "A 쪽이 더 큽니다"만 쓴
        문장이 통과한다. 판정은 값을 대신 말해 주지 않는다.

    이 완화가 근거 밖의 수치를 열지는 않는다:
        여기서 정하는 것은 '무엇이 반드시 있어야 하는가'(하한)뿐이다. '무엇이 있어도 되는가'
        (상한)는 _require_grounded_numbers가 _allowed_numbers로 따로 정하며 그 목록은 바뀌지
        않았다. 보조로 내려간 Fact의 값도 여전히 화이트리스트에 있으므로 문장에 적어도 되고,
        자릿수를 바꿔 적으면 그때는 ungrounded_number로 걸린다.

    TODO:
        사용자가 원천 값 자체를 함께 요청한 경우(예: "원래 값도 알려주고 증감도 알려줘")에는 그
        Fact가 계산의 입력이더라도 답에 실려야 한다. 지금은 그 요청을 구별할 구조적 신호가
        payload에도 AgentExecutionResult에도 없다. query_analyzer.QueryPlan의 metric/derived_metric
        만으로는 갈리지 않으며(그 두 질문은 같은 계획을 만든다), 계획 자체가 grounding 경계를
        넘어오지 않는다. 신호가 생기기 전까지 그 모양은 이 함수가 구별하지 못한다.
    """
    supporting = _facts_consumed_by_calculations(payload)

    for fact in payload.facts:
        if fact.fact_id in supporting:
            continue

        renderings = [fact.display_value]

        if fact.normalized_display:
            renderings.append(fact.normalized_display)

        if not any(rendering in text for rendering in renderings):
            raise AnswerRejectedError(
                "value_dropped", f"Fact {fact.fact_id!r}의 값이 문장에 없습니다"
            )

    for calculation in payload.calculations:
        if calculation.display_value not in text:
            raise AnswerRejectedError(
                "value_dropped",
                f"Calculation {calculation.calculation_id!r}의 값이 문장에 없습니다",
            )

    for history in payload.event_histories:
        if str(history.document_count) not in text:
            raise AnswerRejectedError(
                "value_dropped", f"이력 {history.event_id!r}의 공시 건수가 문장에 없습니다"
            )


def _facts_consumed_by_calculations(payload: GroundedPayload) -> frozenset[str]:
    """
    어떤 계산의 입력으로 쓰인 Fact의 id.

    input_ids에는 Fact와 Calculation의 id가 함께 올 수 있다(Q08의 변화량은 비율 2건을
    가리킨다). 그래서 payload의 Fact id 집합과 교집합을 취한다. 종류를 문자열 모양으로
    짐작하지 않으며, 계산 id가 이 집합에 섞여 계산을 보조로 내리는 일도 없다.

    계산이 하나도 없으면 빈 집합이다. 그때는 어떤 Fact도 보조가 되지 않으므로 단일 Fact
    답변(Q01)과 Fact 비교(Q10)의 완결성 기준은 이 변경 전과 같다.
    """
    known = {fact.fact_id for fact in payload.facts}

    return frozenset(
        input_id
        for calculation in payload.calculations
        for input_id in calculation.input_ids
        if input_id in known
    )


def _require_known_entities(text: str, payload: GroundedPayload) -> None:
    """payload가 말한 기업이 문장에 그대로 나오는지 본다."""
    companies = {fact.company for fact in payload.facts} | {
        calculation.company for calculation in payload.calculations
    }

    missing = sorted(company for company in companies if company not in text)

    if missing:
        raise AnswerRejectedError("entity_dropped", f"문장에 없는 기업입니다: {missing}")


def _require_consistent_accounting_scope(text: str, payload: GroundedPayload) -> None:
    """
    payload가 말하지 않은 회계 기준이 문장에 나오지 않는다.

    Q01의 주요 실패 모드가 연결·별도 혼동이다. 결정론 경로는 확정하지 못한 기준의 구절 자체를
    넣지 않는 것으로 막고(answer_builder.SCOPE_OMITTED_IN_ANSWER), 여기서는 같은 규칙을
    문장 쪽에서 확인한다.

    Fact도 계산도 없는 결과(이력 질문)는 보지 않는다. 회계 기준은 Fact의 속성이고, 그런
    결과에는 기준이라는 축 자체가 없다.
    """
    if not payload.facts and not payload.calculations:
        return

    declared = {fact.scope_label for fact in payload.facts if fact.scope_label} | {
        calculation.scope_label for calculation in payload.calculations if calculation.scope_label
    }

    intruding = sorted(word for word in ACCOUNTING_SCOPE_WORDS - declared if word in text)

    if intruding:
        raise AnswerRejectedError(
            "scope_mismatch", f"payload가 말하지 않은 회계 기준입니다: {intruding}"
        )


def _require_limitation_when_partial(text: str, payload: GroundedPayload) -> None:
    """
    일부만 답한 결과의 한계 진술이 살아 있는지 본다.

    이것이 빠지면 확인하지 못한 범위가 확인된 것처럼 읽힌다. 부분 답변이 완전한 답변으로
    둔갑하는 자리다.
    """
    if payload.limitation and payload.limitation not in text:
        raise AnswerRejectedError("limitation_dropped", "한계 진술이 문장에서 빠졌습니다")


def _require_preserved_relation(text: str, payload: GroundedPayload) -> None:
    """
    문장이 확정된 우열 판정을 뒤집지 않았는지 본다.

    relation이 정본이다. 모델은 그것을 옮기기만 하며, 값을 다시 견주지 않는다
    (answer_builder._comparison_sentence와 같은 원칙). 값은 맞는데 방향만 뒤집힌 문장은
    수치 검증을 그대로 통과하므로, 여기서 보지 않으면 걸리는 자리가 없다.

    판정을 확인할 수 없으면 통과시키지 않고 거절한다. 확인하지 못한 것을 통과시키면 질문이
    요구한 판정이 검증 없이 나가고, 그 답이 맞는지는 아무도 모른다. 거절의 대가는 결정론
    문장이며, 그 문장은 판정을 반드시 담고 있다.
    """
    for comparison in payload.comparisons:
        relation = comparison.relation

        if relation == ComparisonRelation.EQUAL.value:
            _require_no_ranking_claim(text, comparison.comparison_id)
            _require_equality_claim(text, comparison.comparison_id)

            continue

        _require_no_equality_claim(text, comparison.comparison_id)

        sides = _distinguishing_tokens(payload, comparison)

        if sides is None:
            raise AnswerRejectedError(
                "unverifiable_relation",
                f"우열 판정 {comparison.comparison_id!r}의 두 대상을 문장에서 구분할 표기가 "
                "없습니다",
            )

        left_tokens, right_tokens = sides

        if relation == ComparisonRelation.LEFT_GREATER.value:
            winner, loser = left_tokens, right_tokens

        elif relation == ComparisonRelation.RIGHT_GREATER.value:
            winner, loser = right_tokens, left_tokens

        else:
            raise AnswerRejectedError(
                "unverifiable_relation",
                f"확인 방법이 정해지지 않은 relation입니다: {relation!r}",
            )

        _require_ranking_matches(text, comparison.comparison_id, winner=winner, loser=loser)


def _require_no_ranking_claim(text: str, comparison_id: str) -> None:
    """두 값이 같다고 판정된 결과에 한쪽이 크다는 진술이 없어야 한다."""
    stated = sorted(
        {
            expression
            for expression in GREATER_EXPRESSIONS + SMALLER_EXPRESSIONS
            if expression in text
        }
    )

    if stated:
        raise AnswerRejectedError(
            "relation_contradiction",
            f"우열 판정 {comparison_id!r}은 두 값이 같은데 문장이 우열을 말합니다: {stated}",
        )


def _require_equality_claim(text: str, comparison_id: str) -> None:
    """
    두 값이 같다고 판정된 결과는 그 판정을 문장에 담아야 한다.

    우열 표현이 없다는 것만으로 통과시키지 않는다. 값만 나란히 적은 문장은 어느 쪽이 큰지
    묻는 질문에 답하지 않은 것이고, 읽는 사람은 두 수치를 스스로 견줘야 한다. 그 순간 판정을
    하는 것은 시스템이 아니라 사용자가 되며, 확정된 relation은 답변 어디에도 남지 않는다.

    우열 판정을 담지 않은 답변을 막는 규칙(_require_ranking_matches의 relation_dropped)과
    같은 자리이고, 같은 이름으로 걸린다.
    """
    if not any(pattern.search(text) for pattern in EQUALITY_PATTERNS):
        raise AnswerRejectedError(
            "relation_dropped",
            f"우열 판정 {comparison_id!r}은 두 값이 같다는 판정인데 문장이 그것을 말하지 않습니다",
        )


def _require_no_equality_claim(text: str, comparison_id: str) -> None:
    """한쪽이 크다고 판정된 결과에 두 값이 같다는 진술이 없어야 한다."""
    if any(pattern.search(text) for pattern in EQUALITY_PATTERNS):
        raise AnswerRejectedError(
            "relation_contradiction",
            f"우열 판정 {comparison_id!r}은 한쪽이 큰데 문장은 두 값이 같다고 말합니다",
        )


def _require_ranking_matches(
    text: str,
    comparison_id: str,
    *,
    winner: frozenset[str],
    loser: frozenset[str],
) -> None:
    """
    우열 표현마다 그 앞에 놓인 이름이 판정과 맞는지 본다.

    표현 하나를 볼 때 문장 경계까지만 거슬러 올라간다. 앞 문장에는 두 대상의 값이 나란히
    적혀 있어(answer_builder._comparison_sentence), 범위를 넓히면 두 이름이 모두 잡혀 어느
    쪽에 걸린 표현인지 알 수 없게 된다.
    """
    occurrences = [
        (index, polarity)
        for expressions, polarity in (
            (GREATER_EXPRESSIONS, "greater"),
            (SMALLER_EXPRESSIONS, "smaller"),
        )
        for expression in expressions
        for index in _find_all(text, expression)
    ]

    if not occurrences:
        raise AnswerRejectedError(
            "relation_dropped",
            f"우열 판정 {comparison_id!r}이 문장에 나타나지 않습니다",
        )

    for index, polarity in occurrences:
        clause = text[_clause_start(text, index) : index]

        if INVERTING_PARTICLE in clause:
            # "A가 B보다 큽니다"는 표현 앞에 진 쪽이 온다. 좌우를 다시 해석하지 않는다.
            raise AnswerRejectedError(
                "unverifiable_relation",
                f"우열 판정 {comparison_id!r}의 방향을 확인할 수 없는 표현입니다",
            )

        names_winner = any(token in clause for token in winner)
        names_loser = any(token in clause for token in loser)

        if names_winner == names_loser:
            # 둘 다 나오거나(어느 쪽에 걸렸는지 모름) 하나도 나오지 않는(대상이 없음) 절이다.
            raise AnswerRejectedError(
                "unverifiable_relation",
                f"우열 판정 {comparison_id!r}이 가리키는 대상을 문장에서 특정할 수 없습니다",
            )

        expected_winner = polarity == "greater"

        if names_winner is not expected_winner:
            raise AnswerRejectedError(
                "relation_contradiction",
                f"우열 판정 {comparison_id!r}과 반대로 말한 문장입니다",
            )


def _distinguishing_tokens(
    payload: GroundedPayload,
    comparison: GroundedComparison,
) -> tuple[frozenset[str], frozenset[str]] | None:
    """
    두 대상을 문장에서 갈라 볼 표기. 갈리지 않으면 None.

    양쪽이 공유하는 표기는 뺀다. 같은 해의 두 기업을 견줬다면 연도는 둘 다 나오므로 이름을
    가리지 못하고, 한 기업의 두 해를 견줬다면 기업명이 그렇다. 남는 것이 없으면(같은 기업의
    같은 해) 문장만 보고는 어느 쪽 이야기인지 알 수 없으며, 그때는 판정하지 않는다.
    """
    left = _operand_tokens(payload, comparison.left_id)
    right = _operand_tokens(payload, comparison.right_id)

    if left is None or right is None:
        return None

    distinct_left = left - right
    distinct_right = right - left

    if not distinct_left or not distinct_right:
        return None

    return frozenset(distinct_left), frozenset(distinct_right)


def _operand_tokens(payload: GroundedPayload, operand_id: str) -> set[str] | None:
    """견준 대상 한쪽이 문장에서 불릴 만한 표기. payload에 없으면 None."""
    for fact in payload.facts:
        if fact.fact_id == operand_id:
            return _identity_tokens(fact.company, fact.fiscal_year)

    for calculation in payload.calculations:
        if calculation.calculation_id == operand_id:
            return _identity_tokens(calculation.company, calculation.fiscal_year)

    return None


def _identity_tokens(company: str, fiscal_year: int | None) -> set[str]:
    tokens = {company}

    if fiscal_year is not None:
        tokens.add(str(fiscal_year))

    return tokens


def _require_consistent_history_semantics(text: str, payload: GroundedPayload) -> None:
    """
    이력의 셈과 완결 여부를 문장이 뒤집지 않았는지 본다.

    모든 항목을 문장에 적으라고 요구하지 않는다. 무엇을 앞세울지는 질문이 정하고, 여기서
    빠짐없이 적기를 강제하면 답변이 결정론 문장의 어순을 그대로 따라가는 것 말고는 통과할 수
    없게 된다. 정면으로 어긋나는 진술만 막는다.
    """
    for history in payload.event_histories:
        if history.change_count:
            _reject_matching(
                text,
                NO_CHANGE_PATTERNS,
                code="history_contradiction",
                detail=(
                    f"이력 {history.event_id!r}에 변경 {history.change_count}건이 있는데 "
                    "문장은 변경이 없다고 말합니다"
                ),
            )

        else:
            _reject_matching(
                text,
                NO_CHANGE_CONTRADICTIONS,
                code="history_contradiction",
                detail=(
                    f"이력 {history.event_id!r}에 확인된 변경이 없는데 문장은 변경이 있었다고 "
                    "말합니다"
                ),
            )

        if history.chain_complete:
            _reject_matching(
                text,
                CHAIN_INCOMPLETE_CLAIMS,
                code="chain_contradiction",
                detail=f"이력 {history.event_id!r}은 완결됐는데 문장은 미완결이라고 말합니다",
            )

        else:
            _reject_matching(
                text,
                CHAIN_COMPLETE_CLAIMS,
                code="chain_contradiction",
                detail=f"이력 {history.event_id!r}은 미완결인데 문장은 완결됐다고 말합니다",
            )


def _reject_matching(
    text: str,
    patterns: tuple[re.Pattern[str], ...],
    *,
    code: str,
    detail: str,
) -> None:
    """패턴 중 하나라도 문장에 나타나면 거절한다."""
    if any(pattern.search(text) for pattern in patterns):
        raise AnswerRejectedError(code, detail)


def _find_all(text: str, needle: str) -> list[int]:
    """needle이 나타나는 모든 위치."""
    positions: list[int] = []

    start = text.find(needle)

    while start != -1:
        positions.append(start)

        start = text.find(needle, start + 1)

    return positions


def _clause_start(text: str, index: int) -> int:
    """index 앞의 가장 가까운 문장 경계 다음 위치."""
    boundaries = [text.rfind(marker, 0, index) for marker in CLAUSE_BOUNDARIES]

    return max(boundaries) + 1


def _require_no_advisory_wording(text: str) -> None:
    found = sorted({word for word in ADVISORY_EXPRESSIONS if word in text})

    if found:
        raise AnswerRejectedError("advisory_wording", f"투자 판단·예측 표현입니다: {found}")


# --------------------------------------------------------------------------
# 보조
# --------------------------------------------------------------------------


def _allowed_numbers(payload: GroundedPayload) -> set[str]:
    """
    payload가 말한 모든 수치의 canonical 표기.

    기업명과 한계 진술도 넣는다. 숫자가 든 상호가 있고("SK C&C" 계열), 그 숫자는 payload가
    말한 것이므로 문장에 나오는 것이 정상이다. 넣지 않으면 정상 문장이 거절된다.

    질문은 넣지 않는다. 질문에 적힌 수치는 확보한 값이 아니라 요청이며, 그것을 답변에 옮기면
    묻는 말을 되돌려 준 것이 답변의 근거가 된다.
    """
    sources: list[str] = []

    if payload.limitation:
        sources.append(payload.limitation)

    for fact in payload.facts:
        sources.extend([fact.display_value, fact.company])

        if fact.fiscal_year is not None:
            sources.append(str(fact.fiscal_year))

        if fact.normalized_display:
            sources.append(fact.normalized_display)

    for calculation in payload.calculations:
        sources.extend([calculation.display_value, calculation.company])

        if calculation.fiscal_year is not None:
            sources.append(str(calculation.fiscal_year))

    for history in payload.event_histories:
        sources.extend([str(history.document_count), str(history.change_count)])

    allowed: set[str] = set()

    for source in sources:
        allowed.update(_canonical_number(match) for match in NUMBER_PATTERN.findall(source))

    return allowed


def _canonical_number(token: str) -> str:
    """천단위 구분만 지운다. 자릿수는 바꾸지 않는다."""
    return token.replace(",", "")


def _string_tuple(value: Any) -> tuple[str, ...]:
    """신고 목록을 문자열 튜플로. 형태가 어긋나면 빈 값으로 두지 않고 거절한다."""
    if value is None:
        return ()

    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AnswerRejectedError("malformed_id_list", "used_*_ids가 문자열 목록이 아닙니다")

    return tuple(value)


def _strip_code_fence(body: str) -> str:
    """코드 펜스로 감싼 응답을 벗긴다. 형식 지시를 반쯤 따른 응답이 흔하다."""
    if not body.startswith("```"):
        return body

    without_open = body.split("\n", 1)[-1]

    closing = without_open.rfind("```")

    return without_open[:closing].strip() if closing != -1 else without_open.strip()
