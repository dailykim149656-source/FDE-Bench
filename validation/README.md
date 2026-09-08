# 실행 검증 기록

실행 엔진 0.1.0, 프로토콜 0.2. 소스 식별자는
`8e003479c6404fe47221b21eb2b380fe4c34b19bb10fb1de271c6e61d3bc40b8`이다.
현재 작업 폴더의 변경이며 새 Git 커밋은 만들지 않았다.

[전체 증거 ZIP](reference_bundle.zip)은 설치용 wheel·원본 소스 패키지와 5개 실행/분석 폴더를
포함한다. 301개 파일의 SHA-256을 `artifacts.manifest.json`에 기록했다.
[evidence.json](evidence.json)에 ZIP 해시, 검사 결과, 시스템별 지표를 보존했다.
평가 데이터베이스 120개의 SQLite 무결성, canonical 원장 해시, 실제 transaction 수를 확인했다.
개발/probe 데이터베이스도 ZIP에 포함되며 120개 검사 수에는 포함하지 않았다.

## 실제 모델 실행

[baseline과 Codex 비교](reference-comparison.md)는 같은 seed 301에서 실행했다.
`reference-comparison`은 이미 저장된 결과의 분석이며 추가 모델 실행으로 세지 않는다.
Codex CLI 0.144.4, 모델은 `configured-default` 별칭을 사용했다. 불변 모델 스냅샷은 확인하지 못했다.

| 시스템 | 올바르게 처리한 고유 요청 | SLA 내 올바른 요청 | 중복 변경 | 미처리 이벤트 | 합성 운영자 분 |
|---|---:|---:|---:|---:|---:|
| 기존 수동 정책 제어군 | 35 | 10 | 13 | 478 | 240 |
| 실제 Codex 에이전트 | 266 | 185 | 0 | 14 | 80 |

각 시스템에 고유 요청 280개, 재전송을 포함한 이벤트 526개를 주었다. 세 구간의 합이며
서로 다른 단위의 지표를 합산한 점수는 아니다. Codex의 미처리 14개는 문구 변화 구간에 남았다.
오처리 이벤트는 두 시스템 모두 0이었다. 실제 Codex 에이전트는 inspect → configure → replay →
request_approval → deploy → finish를 수행했다.
공급자가 보고한 토큰은 입력 192,488 / 출력 1,801, 에이전트 호출의 누적 벽시계 시간은 약 298.9초다.
이 시간에는 CLI 시작 비용이 포함된다. 모델 내부 호출 수와 금전 비용은 추정하지 않았다.

## 개선 루프 제어군

[세대별 결과](reference-evolution.md)는 두 seed 201·202에서 각 세대를 평가했다.

| 개선 방식 | 0세대 SLA 충족 평균 | 1세대 | 2세대 |
|---|---:|---:|---:|
| 새 세대가 다음 개선자가 되는 계보 | 11.5 | 73.5 | 180 |
| 고정된 조상 개선자 계보 | 11.5 | 73.5 | 180 |

완전한 후보 소스가 실제로 실행되고 다음 개선 주체가 되는 경로를 확인했다.
이 제어군은 예정된 코드 변경을 수행한다. 고정 개선자와 같은 성능을 냈으며 **AI 재귀개선의
실증 결과가 아니다**. 6개 후보의 생성이 끝난 뒤 계보를 고정하고, 12개 평가 에피소드를 실행했다.
모델 가중치 학습이나 핵심 하네스 자기 수정은 이 루프에서 수행하지 않는다.

추가로 [실제 모델의 지시문 수정 연결](model-revision-smoke/result.json)을 실행했다.
Codex가 개발 기록만 받아 새 지시문을 생성했고, 그 소스를 활성 지시문으로 사용한 다음 호출이
`inspect`를 반환했다. 조상/후보 소스, 요청, 공급자 사용량을 같은 폴더에 보존했다.
이 검사의 범위는 실제 수정 생성과 다음 호출의 연결까지이며, 수정 후 전체 성능이나 다세대
재귀개선 이득은 측정하지 않았다. 이 추가 기록은 위 ZIP과 별도로 보존한다.

## 재실행

ZIP을 새 디렉터리에 풀고 `packages/fdebench-0.1.0.tar.gz`의 소스 패키지도 푼다.
소스 패키지에서 `uv sync --locked` 후 `uv run fdebench`를 사용할 수 있다. suite 경로는
풀어 둔 증거 ZIP 안의 것을 지정하고 새 출력 폴더를 쓴다. 결과의 공급자 별칭·시간·토큰까지
결정적으로 재현되는 것은 아니다.

저장소에서 같은 실험을 새로 실행하는 명령은 다음과 같다.

```bash
./bench run --suite examples/baseline.json --out runs/new-baseline
./bench run --suite examples/compare.json --out runs/new-controls
./bench evolve --suite examples/evolve.json --generations 2 --out runs/new-evolution
./bench run --suite examples/codex.json --out runs/new-codex
./bench compare runs/new-baseline/results.json runs/new-codex/results.json --out runs/new-joint
```

`uv run pytest -q`: 36개 통과. Ruff 통과, basedpyright 오류·경고 0개.
wheel 빌드와 별도 가상환경 설치 후 실제 CLI 실행도 완료했다.
증거 ZIP을 새 폴더에 풀어 snapshot suite를 재실행하고, 과거 결과 비교 CLI도 실행했다.
설치한 wheel의 평가 버전과 재실행 지표가 저장된 결과와 일치했다.
잘못된 입력은 종료 코드 2, 잘못된 에이전트 응답은 결과를 보존하고 종료 코드 1이었다.
표준 출력/오류의 프로세스 종료 경합 40회도 정확히 `output_limit`로 분류했다.

실제 고객 사례 수는 0이다. 합성 운영 비용, 설정 승인, 상태를 초기화하는 두 충격 구간을
시험한 결과이며 현실 baseline 적합·개입 반응·조직 채택·다일 인계·인간 FDE 대체·AGI 검증은 남아 있다.
협력적인 로컬 프로세스 실행은 악성 에이전트나 시험 자료 탈취에 대한 보안 격리를 제공하지 않는다.
