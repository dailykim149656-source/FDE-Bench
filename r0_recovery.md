# R0 재현 기록

확인일: 2026-09-08. 상태: **R0_REFERENCE_REPRODUCED**. 사용자 제공 `FDE_Bench_v0.2_Protocol_and_Reference_Tests.zip`에서 원 파일 5개를 복구했다. 원 추정기를 변경 없이 `--seeds 1000`으로 실행해 **20/20 통과와 §13 수치 일치**를 확인했다. 실패·오류·건너뛴 검사는 각각 0개다.

[원 결과](r0_reference/original/results.json), [재실행 결과](r0_reference/results.reproduced.json), [버전·해시·비교 메타데이터](r0_reference/reproduction.json)를 함께 보존한다. 결과 JSON의 `passed`는 합성 산술·불변성 fixture 검사에만 해당한다. `human_scope=none`, `organization_coverage=none`, `independent_fde_case_families=0`이다.

| 항목 | 원 보고 | 재실행 |
|---|---:|---:|
| 참조 검사 | 20/20 | 20/20 |
| 동일 정책·비용 A/A | 0 | 0 |
| 외생 호황의 단순 전후 증가 | +8,000 | +8,000 |
| 같은 호황의 baseline 대비 무개입 기여 | 0 | 0 |
| 알려진 양의 효과 | +39,000 | +39,000 |
| 알려진 손해 | −21,000 | −21,000 |
| 인계 후 유지·붕괴 정책의 사후 가치 차이 | 30,000 | 30,000 |

수치는 합성 회계 단위다. −21,000은 원 JSON의 demonstrations 항목에 없으며 검사 04가 확인한다. 이번 복구에서는 seed 0의 반환값도 직접 대조했다.

## 코드·결과 식별

- 복구 커밋: `77131d2ddbee80dc4d76f559e7a23f6b9abd94e6`. 이는 이번에 만든 로컬 Git 기록이며 과거 실행 당시의 커밋이 아니다.
- 소스 SHA-256 및 `evaluator_version`: `sha256:f4faf64b00026a5becbac6b29449505bd2d1d25e71287e4ada6b347b97d712e1`.
- 원본은 `r0_reference/original/`에 보존했다. 제공된 MANIFEST의 4개 파일 해시와 모두 일치하며, MANIFEST 자체와 ZIP의 해시도 재현 메타데이터에 기록했다. 이는 제공 패키지 내부의 무결성 대조다.
- 원 결과는 Python 3.13.5 / Linux, 새 실행은 Python 3.14.6 / macOS다. JSON의 필드 집합, 모든 결정적 값, demonstrations 전체와 canonical trace가 일치한다. 개별 검사 이름·상태도 일치하며 로그의 경과 시간만 다르다.

**원 결과에도 이미 `source_sha256`이 있었다.** 원 파일이 로컬에 없다는 이유로 원 실행에 코드 식별 근거가 전혀 없었다고 해석해서는 안 된다. 다만 §11.1 식별자 전체가 원 결과에 명시돼 있지는 않다. 이번 `reproduction.json`에서 evaluator·suite·detector를 원 소스 해시에 연결하고, 나머지 식별자와 비적용 이유를 보완했다. 두 결과 파일을 각 해시로 특정하는 별도 메타데이터이며 원 실행 기록을 소급해 바꾼 것이 아니다.

평가기 코드는 수정하지 않았다. 따라서 이번 대조는 **같은 평가기 바이트의 다른 실행 환경에서의 재현**이다. §11.3의 서로 다른 평가기·환경·모델 버전 사이 측정 불변성은 확인하지 않았다.

## 검사 이름의 해석 제한

- `seeds_per_looped_check=1000`: 검사 01–06·09–10은 각 1,000 seed, 검사 11은 100 seed만 쓴다. 나머지는 단일 fixture나 파라미터 검사다. 20개 검사 모두를 1,000번 반복했다고 보고하지 않는다.
- 검사 15의 “동등 구현”: 같은 정책 파라미터에서 이름만 다르게 준다. 서로 다른 소프트웨어 구현을 실행한 검사가 아니다.
- 양·음 효과와 인계 지속 여부는 작성자가 정의한 정책 값이다. 1,000 seed는 고객 사례 1,000개가 아니며 실제 개입 반응·인과 귀속이나 twin fidelity의 근거가 아니다.
- 탐지 검사는 합성 정답 이벤트를 읽고 가정한 민감도의 수식을 계산한다. 현실 탐지기의 교정 실험은 없다.

원 코드와 결과는 보존하고 이 제한을 메타데이터·README·v0.2 주석에 반영했다. R1 도구나 검사를 추가하지 않았다.

## 다시 실행하고 대조하기

프로젝트 루트에서 실행한다. 기록된 두 결과를 보존하도록 로컬 출력 파일을 따로 쓴다.

```bash
python3 r0_reference/original/estimator_reference.py --seeds 1000 --out r0_reference/results.local.json
python3 - <<'PY'
import json
import re
from pathlib import Path

original = json.loads(Path('r0_reference/original/results.json').read_text())
local = json.loads(Path('r0_reference/results.local.json').read_text())
assert set(original) == set(local)
for result in (original, local):
    del result['python_version'], result['platform']
    result['test_log'] = re.sub(
        r'Ran 20 tests in [0-9.]+s', 'Ran 20 tests in <duration>s', result['test_log'])
assert original == local, 'R0 reference mismatch'
print('R0 reference comparison matches; synthetic fixtures only')
PY
```

새 출력은 새 실행 기록이다. 인용·배포할 때는 해당 출력 해시와 실행 조건을 담은 메타데이터를 별도로 남긴다. 기존 `reproduction.json`을 다른 해시의 출력에 붙여 쓰지 않는다. CLI의 `--help`는 종료 코드 0, `--seeds 0`은 명시적 오류와 종료 코드 2를 확인했다.

## 상태 정정과 다음 단계

앞서 로컬 검색과 ChatGPT 라이브러리 자동 다운로드로 파일을 확보하지 못해 `UNVERIFIED_DOWNLOAD_BLOCKED`로 표시했다. 사용자 제공 ZIP으로 그 접근 문제가 해소됐다. 당시 실패는 수치 오류의 증거가 아니었고, 이번 재실행에서 원 수치와 일치했다. Downloads 원문과 복구한 배포 파일은 그대로이며 archive v0.2에는 상태 정정 주석만 갱신한다. v0.3는 만들지 않는다.

현재 임계 경로는 **실제 변경 전·전환 중·후 운영자료를 가진 실무자와의 최초 인터뷰 2–3건**이다. [인간 평가 범위 결정](human_evaluation_decision.md)과 [자료 요청 초안](r1_fieldwork/outreach.md)은 준비돼 있다. 연락·인터뷰·프로젝트 선정·실행형 twin 구축은 아직 이루어지지 않았다.
