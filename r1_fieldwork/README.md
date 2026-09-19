# R1 조사 실행 안내

이 조사는 실행형 FDE 벤치마크의 첫 사례를 만들기 위한 자료 수집 단계다. 최종 산출물은 모델을 실행하고 실제 프로젝트 성과를 평가·비교하는 패키지이며 인터뷰나 baseline 검증 자체가 개발의 종료 조건은 아니다. [벤치마크 사용판 완료 조건](../README.md)을 따른다.

실제 배치 1건을 시간순으로 복원한다. **먼저 인터뷰 2–3건**으로 실제 자료 접근성과 잠정 질문표의 적합성을 확인한다. 전체 조사 목표는 5–6명, 각 1–2개 프로젝트다. 연락처나 경력기술서만으로 인터뷰나 사례 수를 채우지 않는다.

1. [연락 목록](contact_list.md)을 익명 코드로 채우고 [요청문 초안](outreach.md)을 검토한다. 현재 발송된 요청은 없다.
2. [인터뷰 스크립트](interview_script.md)의 잠정 8개 항목을 참고한다. 실제 사례를 그 분류에 억지로 맞추지 않는다. 60분은 조사 일정 가정이며 인간 평가 에피소드가 아니다.
3. cases/_template.json을 cases/의 새 익명 파일로 복사한다. status는 draft로 시작한다. 원자료 확인 뒤 collected로 변경한다.
4. 공유가 허용된 증거 사본은 private_evidence/ 또는 별도 로컬 디렉터리에 둔다. artifacts에는 파일별 id, kind, 상대 path, SHA-256, 근거 위치 locator를 기록한다.
5. 준비 상태 검사기를 실행하고 빠진 증거를 보완한다. [사례 검토표](case_review.md)로 원자료의 의미, baseline 복원과 과거 개입 반응의 검증 가능성을 검토한 뒤 프로젝트 하나만 선정한다.

첫 2–3건 뒤에는 맞지 않은 질문·누락된 작업·자료가 분리되지 않는 이유를 원래 설명과 함께 검토한다. JSON에 바로 옮길 수 없으면 공유 가능한 익명 서술을 private_evidence/에 보존한다. 형식 부적합을 실사례 부적합으로 판정하지 않는다. 이 점검 전에는 스키마 필수항목이나 검사를 더 추가하지 않는다.

접근 요청에는 **실제 변경이 최소 하나 포함된 전·전환 중·후 자료**, 변경 시점·적용 대상·정책/코드 버전, 같은 거래를 연결할 익명 식별자, 외부 충격·동시 변경을 포함한다. 비교 가능한 미변경 대상·기간 또는 단계적 배포/롤백 기록이 있는지도 확인한다. 자료의 존재와 공유 가능 여부를 먼저 묻고, 결과가 나빴다는 이유로 제외하지 않는다.

```bash
python3 r1_fieldwork/select_case.py --help
python3 r1_fieldwork/select_case.py --cases r1_fieldwork/cases --evidence-root r1_fieldwork/private_evidence --json
python3 r1_fieldwork/self_check.py
```

기본 증거 디렉터리가 비어 있거나 없으면 해당 자료는 INCOMPLETE로 표시한다. 별도 저장 위치는 --evidence-root로 지정한다. 파일 해시는 macOS의 shasum -a 256 명령 등으로 계산할 수 있다.

| 출력 | 의미 |
|---|---|
| INVALID | JSON/자료형/해시/경로 오류 또는 중복 식별자. 해당 실행의 종료 코드 2. 조용히 누락하지 않는다. |
| EXCLUDED | 합성 fixture. cases/로 옮겨도 실사례로 인정하지 않는다. |
| INCOMPLETE | 초안, 미확인 사실, 확인 가능한 증거 부족. 후속 조사가 가능하며 부적합 확정이 아니다. |
| OUT_OF_SCOPE | 근거를 적어 baseline 복원 불가능을 확인했거나 첫 사례의 승인 경로 1개 범위를 벗어남. 더 작은 범위 검토 가능. |
| READY_FOR_REVIEW | 최소 기록과 증거 사본이 연결되어 사람이 내용 검토를 시작할 수 있음. 선정·인과 식별·twin 검증 통과가 아니다. |

종료 코드 0은 목록 검사를 실행했다는 뜻이며 모든 사례의 적격 판정이 아니다. 자료가 없으면 목록도 비어 있다. 선택기는 임의 점수, 자동 순위, 자동 선정, 사례군 수 인증을 하지 않는다.

## 기록 형식

현재 intake 형식은 **schema_version=2, PROVISIONAL(실무 검토 전 잠정)**이다. 프로토콜에서 출발한 질문표이며 실무자가 확정한 환경 축이 아니다. 이는 데이터 입력 형식이며 연구 프로토콜 v0.3가 아니다. 첫 2–3건 이후 구조를 바꾸면 입력 형식 버전과 변경 이유를 기록하고 원 서술을 보존한다. 문구 설명만 바꾸는 이번 수정은 형식 버전을 올리지 않는다. 실제 입력 기록은 현재 0건이므로 기존 boolean 형식은 자동 변환하지 않는다. 옛 형식은 INVALID로 표시한다. 과거의 true 값을 증거로 변환하지 않는다.

실행 엔진 ontology(`fdebench.execution-ontology.v0.1`)와 이 현장 ontology는 별개다. 사례 기록은 둘 사이의 대응이지, 현장 분류를 엔진 스키마로 고정하는 작업이 아니다. 특히 현장의 `handoff`는 에이전트 `finish` 노트가 아니라 배포 이후 연속성이다. `π₀`는 incumbent 객체와도, baseline 실행 결과와도 구분한다. 대응표는 `./bench ontology`의 `field_section_map`이다.

- source.origin: observed / self_report / synthetic. observed는 관측자료를 확인했다고 작성자가 기록하는 값이다. 프로그램이 진위를 보증하지 않는다.
- sections: initial_state, pi0_evidence, intervention, deployment_adoption, outcome, handoff, external_shocks, no_intervention 각각 summary와 evidence_ids.
- baseline: reconstructable은 true/false/null. null은 미확인, false는 reason 필수. runbook_ids, preperiod_ids, workload_ids는 관측자료에 연결하고 validation_plan_ids는 작성한 검증계획 사본에 연결한다.
- artifacts.kind: observed는 당시 관측자료, recollection은 회고, synthetic은 생성자료, protocol은 조사자가 작성한 검증계획. 회고·생성자료는 baseline 관측 증거를 충족시키지 못한다.

증거 항목 예시 구조: id, kind, path, sha256, locator. 빈 배열에서 시작해 파일이 있을 때만 추가한다. locator에는 시트/행/쿼리/기간 등 재확인할 위치를 적는다. 파일이 같아도 다른 주장을 뒷받침하려면 해당 위치와 의미를 검토표에 설명한다.

해시는 같은 파일을 다시 확인하기 위한 수단이다. 파일 내용이 실제 로그인지, 원인이 식별되는지, 날짜가 맞는지, 검증계획이 실행됐는지는 사람이 확인한다. 테스트용 합성 파일을 observed로 거짓 표기하는 행동까지 자동으로 판별할 수는 없다.

## 사례 선정과 환경 검증의 구분

인계 실패·배포 실패·성과 악화도 조사 기록으로 남긴다. 이력이 성공적이었다는 이유로 점수를 주지 않는다. 첫 환경에 유익한 개입이 존재하는지는 별도의 알려진 정책 검사로 확인한다. 실패한 실제 배치에서 더 나은 유효 개입을 구성할 수도 있다.

보정용 기간과 보류 검증 기간, 입력 workload와 정책 결과, 허용오차의 근거를 사례 선정 전에 정한다. 숫자는 실제 자료 확인 후 넣는다. 검사기는 검증계획 파일의 존재만 확인하므로, 본문이 빈 서식인 경우 사람 검토에서 보완 대상으로 남긴다. 과거 개입 구간의 증거는 기존 sections.intervention / outcome / external_shocks의 evidence_ids로 연결한다. 현재 CLI는 그 내용이나 개입 효과 식별을 검사하지 않으므로 READY_FOR_REVIEW여도 첫 환경으로 선정되지 않을 수 있다.

첫 사례군 1개는 twin의 baseline 재현에 더해 실제 수정 → transaction 경로 변화 → 독립 품질 검사 → 인계 후 충격 2개의 결과가 실행 원장에 남고 재현될 때만 기록한다. READY_FOR_REVIEW나 단독 baseline 적합은 사례군 1개가 아니다.
