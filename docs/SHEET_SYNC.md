# 구글 시트 연동 (키워드 실험실)

구글 시트의 콘텐츠로 메타 4종(리드문·엔티티·인텐트·카테고리)과 핵심키워드를 만들고, 결과를 같은 시트 행에 다시 적는다.
**시트가 프리즘을 부른다.** 시트에 붙은 Apps Script(Code.gs)가 `POST <프리즘 주소>/sheet-sync` 를 호출한다.
(사내 계정은 Apps Script 웹앱을 외부 공개로 배포할 수 없어, 프리즘이 시트를 부르는 방식은 쓰지 않는다)

- 사용 조건: 핵심 키워드 프롬프트 버전 1개 · 모델 1개(연결 코드를 만들 때 STEP 2 에서 고른 조합으로 고정)
- 한 번에 최대 100건 · 넘는 행은 다음 실행에서 처리
- 결과는 키워드 실험실 실험 기록(미검수 · 실행자 `구글 시트`)으로도 남는다 · 검수는 실험실 검수 화면에서
- 시트에서 고친 검수 결과를 프리즘으로 가져오는 동기화는 2단계(이번 범위 아님)

## 설정 절차

1. 프리즘 키워드 실험실 STEP 2 에서 **단일 모델 · 핵심 키워드** 로 모델과 프롬프트 버전을 고른다.
2. STEP 3 입력 방식 **구글 시트 연동**에서 **연결 코드 만들기**를 누른다. 코드는 이 화면에서 한 번만 보인다.
   다시 만들면 이전 코드는 바로 막힌다(팀에 코드 1개 · 버전·모델을 바꿀 때도 다시 만든다).
3. **템플릿 사본 만들기**로 내 드라이브에 사본을 만든다(Code.gs 가 들어 있다).
4. 사본 시트 메뉴 **프리즘 › 양식 만들기**를 누른다. 처음 한 번은 권한 승인 창이 뜬다(외부 서비스 연결 포함).
5. 메뉴 **프리즘 › 연결 설정**에 2번의 코드를 붙여 넣는다.
6. 입력 열을 채우고 **프리즘 › 실행**을 누른다. 몇 분 뒤 **프리즘 › 결과 받기**를 누르면 결과 열이 채워진다.
   실행 중에 눌러도 끝난 행부터 적는다 · 다시 눌러도 같은 값을 다시 적을 뿐이다.

연결 코드는 `<프리즘 주소>/sheet-sync#<팀>.<비밀>` 한 줄이다. 남에게 보이지 않게 다룬다.
프리즘은 코드의 sha256 만 저장한다.

## 메뉴

| 메뉴 | 하는 일 |
|---|---|
| 양식 만들기 | 머리줄·서식·상태 목록 생성 · 있는 머리줄은 덮지 않고 없는 열만 끝에 추가 |
| 연결 설정 | 연결 코드 저장(스크립트 속성 `PRISM_CODE`) |
| 실행 | 상태가 빈 행·`실패` 행을 프리즘으로 보내 실험 시작 |
| 미검수 포함 다시 실행 | 위 + `미검수` 행 |
| 결과 받기 | 마지막 실행의 결과를 행에 적고 기재 건수를 프리즘에 알림 |

## 열

| 구분 | 열 이름 | 내용 |
|---|---|---|
| 입력 | 콘텐츠키 | 콘텐츠 고유 키 |
| 입력 | 서비스 | 서비스 코드 |
| 입력 | 제목 | 필수(제목·본문 중 하나 이상) · 300자 이하 |
| 입력 | 본문 | 20,000자 이하 |
| 입력 | 원문 링크 | 원문 주소 |
| 결과 | 리드문 | 메타 리드문 |
| 결과 | 엔티티 | 여러 값은 `, ` 로 구분 |
| 결과 | 인텐트 | 여러 값은 `, ` 로 구분 |
| 결과 | 카테고리 | 여러 값은 `, ` 로 구분 |
| 결과 | 핵심키워드 | 여러 값은 `, ` 로 구분 |
| 결과 | 핵심문장 | 추가 모델 호출 없이 얻을 수 있을 때만 |
| 결과 | 모델 | `메타 <모델> · 키워드 <모델>` |
| 결과 | 처리 시각 | KST `YYYY-MM-DD HH:MM` |
| 결과 | 상태 | `미검수` · `실패` (`검수 완료` · `반영됨` 은 2단계 예약) |
| 결과 | 프리즘 해시 | 프리즘 콘텐츠 해시 |
| 결과 | 실험 ID | 키워드 실험실 실험 ID |
| 결과 | 오류 | 실패 사유 |

열 순서는 `setup` 이 만든 순서(위 표 순서)다. 이름으로 찾으므로 열을 옮겨도 동작하지만 이름은 바꾸지 않는다(앞뒤 공백 무시).
`상태` 열과 `제목`·`본문` 중 하나가 없으면 실행이 `양식이 다릅니다` 로 멈춘다.

## 다시 실행할 때 고르는 행

- 상태가 빈 행과 `실패` 행을 처리한다
- `미검수` 행은 건너뛴다 · 메뉴 **미검수 포함 다시 실행**이면 포함
- `검수 완료` · `반영됨` 행은 항상 건너뛴다
- 시트는 상태로 거른 행을 최대 300행까지 보낸다 · 대상이 100건을 넘으면 시트 행 순서로 100건만 처리하고 나머지는 다음 실행으로 미룬다
- 실행 중에는 정렬·행 삽입·삭제를 하지 않는다 · 결과 받기 때 제목·콘텐츠키가 달라진 행은 적지 않는다

## 프리즘 API (`POST /sheet-sync`)

로그인 대신 본문 `key`(연결 코드 `#` 뒤)로 팀을 정한다. 코드가 틀리면 형식·팀·비밀 어느 쪽이든 같은 오류를 돌려준다.

| op | 본문 | 응답 |
|---|---|---|
| `run` | `rows:[{row, values:{열 이름: 값}}]` · `include_pending` | `{ok, id, targets, skipped}` |
| `result` | `id` | `{ok, status, total, updates:[{row, values, expect}]}` · 끝난 행만 |
| `written` | `id, written, mismatched` (정수) | `{ok}` · 실험 기록 화면에 기재 건수 표시 |

## Code.gs

```javascript
// 프리즘 키워드 실험실 · 구글 시트 연동(시트 → 프리즘)
// 메뉴 프리즘 › 양식 만들기 · 연결 설정 · 실행 · 미검수 포함 다시 실행 · 결과 받기
// POST <프리즘>/sheet-sync {key, op:'run'|'result'|'written', ...}

var INPUT_COLS = ['콘텐츠키', '서비스', '제목', '본문', '원문 링크'];
var RESULT_COLS = ['리드문', '엔티티', '인텐트', '카테고리', '핵심키워드', '핵심문장', '모델', '처리 시각', '상태', '프리즘 해시', '실험 ID', '오류'];
var STATES = ['미검수', '실패', '검수 완료', '반영됨'];
var MAX_SEND = 300;

function sheet_() {
  return SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
}

function headers_(sh) {
  var lastCol = sh.getLastColumn();
  if (lastCol < 1) return [];
  var head = sh.getRange(1, 1, 1, lastCol).getDisplayValues()[0].map(function (h) { return String(h).trim(); });
  return head.join('') ? head : [];
}

function onOpen() {
  SpreadsheetApp.getUi().createMenu('프리즘')
    .addItem('양식 만들기', 'setupMenu')
    .addItem('연결 설정', 'connect')
    .addSeparator()
    .addItem('실행', 'runNew')
    .addItem('미검수 포함 다시 실행', 'runPending')
    .addItem('결과 받기', 'fetchResult')
    .addToUi();
}

function setupMenu() {
  setup();
  SpreadsheetApp.getUi().alert('프리즘 양식을 만들었습니다. 프리즘 › 연결 설정에 연결 코드를 넣고 입력 열을 채우세요.');
}

// 메뉴 또는 편집기에서 실행 · 프리즘 양식(머리줄·서식·상태 목록)을 만든다 · 있는 머리줄은 덮지 않고 없는 열만 끝에 추가
function setup() {
  var sh = sheet_();
  var head = headers_(sh);
  if (!head.length) sh.setName('프리즘 연동');   // 이미 쓰던 시트면 이름은 그대로 둔다
  INPUT_COLS.concat(RESULT_COLS).forEach(function (name) {
    if (head.indexOf(name) < 0) {
      head.push(name);
      sh.getRange(1, head.length).setValue(name);
    }
  });
  sh.setFrozenRows(1);
  sh.getRange(1, 1, 1, head.length).setFontWeight('bold');
  INPUT_COLS.forEach(function (name) {
    sh.getRange(1, head.indexOf(name) + 1).setBackground('#e8f0fe');
  });
  RESULT_COLS.forEach(function (name) {
    sh.getRange(1, head.indexOf(name) + 1).setBackground('#fef7e0').setNote('프리즘이 기록하는 칸');
  });
  var stateCol = head.indexOf('상태') + 1;
  var rule = SpreadsheetApp.newDataValidation().requireValueInList(STATES, true).setAllowInvalid(false).build();
  sh.getRange(2, stateCol, Math.max(sh.getMaxRows() - 1, 1), 1).setDataValidation(rule);
}

function connect() {
  var ui = SpreadsheetApp.getUi();
  var r = ui.prompt('연결 설정', '프리즘 키워드 실험실 STEP 3 에서 만든 연결 코드를 붙여 넣으세요.', ui.ButtonSet.OK_CANCEL);
  if (r.getSelectedButton() !== ui.Button.OK) return;
  var code = r.getResponseText().trim();
  if (!/^https:\/\/[^#\s]+\/sheet-sync#[\w-]+\.[\w-]{40,64}$/.test(code)) {
    ui.alert('연결 코드 형식이 다릅니다 · 프리즘 화면의 코드를 그대로 복사하세요');
    return;
  }
  PropertiesService.getScriptProperties().setProperty('PRISM_CODE', code);
  ui.alert('연결했습니다. 입력 열을 채우고 프리즘 › 실행을 누르세요.');
}

function call_(body) {
  var code = PropertiesService.getScriptProperties().getProperty('PRISM_CODE');
  if (!code) throw new Error('먼저 프리즘 › 연결 설정을 하세요');
  var i = code.indexOf('#');
  body.key = code.slice(i + 1);
  var res = UrlFetchApp.fetch(code.slice(0, i), {method: 'post', contentType: 'application/json',
    payload: JSON.stringify(body), muteHttpExceptions: true});
  var data;
  try { data = JSON.parse(res.getContentText()); } catch (e) { throw new Error('프리즘 응답을 읽지 못했습니다 (HTTP ' + res.getResponseCode() + ')'); }
  if (!data.ok) throw new Error(data.error || '프리즘이 오류를 돌려줬습니다');
  return data;
}

function runNew() { run_(false); }
function runPending() { run_(true); }

// 상태로 먼저 거른 행의 입력 열·상태만 보낸다(본문 크기) · 대상 선별은 프리즘이 다시 한다
function run_(pending) {
  var ui = SpreadsheetApp.getUi();
  var sh = sheet_(), head = headers_(sh), st = head.indexOf('상태');
  if (st < 0 || (head.indexOf('제목') < 0 && head.indexOf('본문') < 0)) {
    ui.alert('양식이 다릅니다 · 프리즘 › 양식 만들기를 먼저 누르세요');
    return;
  }
  var allowed = pending ? ['', '실패', '미검수'] : ['', '실패'];
  var cols = INPUT_COLS.concat(['상태']).filter(function (n) { return head.indexOf(n) >= 0; });
  var rows = [], lastRow = sh.getLastRow();
  var data = lastRow >= 2 ? sh.getRange(2, 1, lastRow - 1, head.length).getDisplayValues() : [];
  for (var i = 0; i < data.length && rows.length < MAX_SEND; i++) {
    if (allowed.indexOf(String(data[i][st]).trim()) < 0) continue;
    var values = {}, filled = false;
    cols.forEach(function (n) {
      values[n] = data[i][head.indexOf(n)];
      if (n !== '상태' && values[n] !== '') filled = true;
    });
    if (filled) rows.push({row: i + 2, values: values});
  }
  if (!rows.length) {
    ui.alert('처리할 행이 없습니다');
    return;
  }
  try {
    var out = call_({op: 'run', rows: rows, include_pending: pending});
    PropertiesService.getScriptProperties().setProperty('PRISM_RUN', out.id);
    ui.alert('실행을 시작했습니다', '처리 대상 ' + out.targets + '건 · 건너뜀 ' + out.skipped + '건\n\n' +
      '몇 분 뒤 프리즘 › 결과 받기를 누르세요. 그동안 정렬·행 삽입·삭제는 하지 마세요.', ui.ButtonSet.OK);
  } catch (e) {
    ui.alert('실행 실패', e.message, ui.ButtonSet.OK);
  }
}

// 모델 결과가 = + - @ 로 시작하면 수식으로 해석되지 않게 글자로 적는다
function safe_(v) {
  var s = v == null ? '' : String(v);
  return /^[=+\-@]/.test(s) ? "'" + s : s;
}

// 보낼 때와 같은 행인지 확인(정렬·행 삽입으로 위치가 바뀌면 다른 콘텐츠에 덮어쓰지 않게) · 빈 값끼리는 일치
function sameRow_(sh, head, row, expect) {
  if (!expect) return true;
  for (var name in expect) {
    if (!Object.prototype.hasOwnProperty.call(expect, name)) continue;
    var col = head.indexOf(String(name).trim()) + 1;
    var now = col ? String(sh.getRange(row, col).getDisplayValue()).trim() : '';
    if (now !== String(expect[name] == null ? '' : expect[name]).trim()) return false;
  }
  return true;
}

function write_(updates) {
  var sh = sheet_(), head = headers_(sh), written = 0, mismatched = 0;
  updates.forEach(function (u) {
    var row = Number(u.row);
    if (!(row >= 2) || Math.floor(row) !== row || !u.values) return;
    if (!sameRow_(sh, head, row, u.expect)) { mismatched++; return; }
    for (var name in u.values) {
      if (!Object.prototype.hasOwnProperty.call(u.values, name)) continue;
      var col = head.indexOf(String(name).trim()) + 1;
      if (!col) {
        head.push(String(name).trim());
        col = head.length;
        sh.getRange(1, col).setValue(head[col - 1]);
      }
      sh.getRange(row, col).setValue(safe_(u.values[name]));
    }
    written++;
  });
  SpreadsheetApp.flush();
  return {written: written, mismatched: mismatched};
}

function fetchResult() {
  var ui = SpreadsheetApp.getUi();
  var id = PropertiesService.getScriptProperties().getProperty('PRISM_RUN');
  if (!id) {
    ui.alert('실행한 기록이 없습니다 · 프리즘 › 실행을 먼저 누르세요');
    return;
  }
  var lock = LockService.getDocumentLock();
  if (!lock.tryLock(30000)) {
    ui.alert('다른 사람이 결과를 적는 중입니다 · 잠시 뒤 다시 누르세요');
    return;
  }
  try {
    var out = call_({op: 'result', id: id});
    var w = write_(out.updates || []);
    call_({op: 'written', id: id, written: w.written, mismatched: w.mismatched});
    var msg = (out.status === 'running' ? '아직 실행 중입니다 · 끝난 ' : '') + w.written + '건을 적었습니다 (전체 ' + out.total + '건)';
    if (w.mismatched) msg += '\n위치가 바뀐 ' + w.mismatched + '행은 적지 않았습니다 · 정렬·삽입을 되돌리고 다시 누르세요';
    ui.alert('결과 받기', msg, ui.ButtonSet.OK);
  } catch (e) {
    ui.alert('결과 받기 실패', e.message, ui.ButtonSet.OK);
  } finally {
    lock.releaseLock();
  }
}
```

## 부록: 템플릿 없이 직접 만들기

1. 새 구글 시트를 만들고 **확장 프로그램 › Apps Script** 를 연다. `Code.gs` 내용을 위 코드로 바꿔 저장한다.
2. 시트 탭을 새로고침하면 메뉴 **프리즘**이 생긴다. 이후는 설정 절차 4단계부터 같다.

템플릿 원본을 만드는 사람은 위 1번까지만 하고 **연결 설정**을 하지 않는다(사본에 연결 코드가 따라가지 않게).
공유 링크 끝의 `/edit…` 를 `/copy` 로 바꾼 주소가 템플릿 사본 링크다. 웹앱 배포는 필요 없다.

## 보안

- 연결 코드는 시트 스크립트 속성 `PRISM_CODE` 에만 둔다 · 프리즘은 sha256 만 저장하고 화면엔 발급 직후 한 번만 보인다
- 시트 편집자는 Apps Script 에서 코드를 볼 수 있다 · 편집 권한은 팀 안에서만 준다
- 코드가 새면 프리즘에서 **연결 코드 다시 만들기** → 이전 코드는 즉시 막힌다
- 코드 하나로 할 수 있는 일: 묶인 버전·모델로 실험 시작(실모델 비용) · 그 팀 시트 실험 결과 조회 · 팀당 동시 실행 1개
