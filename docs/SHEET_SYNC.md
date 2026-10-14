# 구글 시트 연동 (키워드 실험실)

키워드 실험실 STEP 3 입력 방식 **구글 시트 연동**은 시트의 콘텐츠를 읽어 메타 4종(리드문·엔티티·인텐트·카테고리)과
핵심키워드를 만들고, 결과를 같은 시트 행에 다시 적는다. 프리즘은 시트에 붙인 Apps Script 웹앱 하나만 호출한다.

- 사용 조건: 단일 모델 테스트 · 대상 키워드
- 한 번에 최대 100건 · 넘는 행은 다음 실행에서 처리
- 결과는 키워드 실험실 실험 기록(미검수)으로도 남는다 · 검수는 실험실 검수 화면에서
- 시트에서 고친 검수 결과를 프리즘으로 가져오는 동기화는 2단계(이번 범위 아님)

## 설정 절차

1. 프리즘 키워드 실험실 STEP 3 에서 **구글 시트 연동**을 고르고 **템플릿 사본 만들기**를 누른다.
   구글이 내 드라이브에 사본을 만든다(프리즘 코드가 이미 들어 있다).
2. 사본 시트 메뉴 **프리즘 › 양식 만들기**를 누른다. 처음 한 번은 권한 승인 창이 뜬다.
   첫 시트 이름이 `프리즘 연동` 으로 바뀌고 1행에 입력 5열 · 결과 12열 머리줄이 생긴다
   (입력 열과 결과 열은 배경색으로 구분 · `상태` 열은 목록에서만 고름 · 있는 머리줄은 덮지 않고 없는 열만 추가).
   입력 열 5개에 콘텐츠를 채운다.
3. 메뉴 **확장 프로그램 › Apps Script** 에서 **배포 › 새 배포 › 유형: 웹 앱** · 실행 사용자 **나** ·
   액세스 권한 **링크가 있는 모든 사용자** 로 배포한다.
4. 시트로 돌아와 메뉴 **프리즘 › 연결 코드 보기**를 누르고 나온 코드를 복사한다.
   비밀 키가 없으면 이때 자동으로 만들어 스크립트 속성 `PRISM_KEY` 에 저장한다. 배포 전이면 먼저 배포하라는 안내가 뜬다.
5. 프리즘 STEP 3 **연결 코드** 칸에 붙여 넣고 **설정 저장** → **시트 불러오기**로 처리 대상과 건너뛸 행을 확인한 뒤
   STEP 4 에서 실행한다.

연결 코드는 `웹앱 주소#비밀 키` 한 줄이다. 남에게 보이지 않게 다룬다.
코드를 고친 뒤에는 **배포 관리 › 수정 › 새 버전**으로 다시 배포해야 반영된다(주소는 그대로).

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
`상태` 열과 `제목`·`본문` 중 하나가 없으면 웹앱이 `양식이 다릅니다 · setup 을 실행하세요` 오류를 돌려준다.

## 다시 실행할 때 고르는 행

- 상태가 빈 행과 `실패` 행을 처리한다
- `미검수` 행은 건너뛴다 · 화면에서 **미검수도 다시 생성**을 켜면 포함
- `검수 완료` · `반영됨` 행은 항상 건너뛴다
- 대상이 100건을 넘으면 시트 행 순서로 100건만 처리하고 나머지는 다음 실행으로 미룬다
- 실행 중에는 정렬·행 삽입·삭제를 하지 않는다 · 읽을 때와 제목·콘텐츠키가 달라진 행은 기록하지 않는다

## Code.gs

```javascript
// 프리즘 키워드 실험실 · 구글 시트 연동 웹앱
// 시트 메뉴 프리즘 › 양식 만들기(setup) · 연결 코드 보기(connectCode)
// GET  ?key=비밀키  → {ok, rows:[{row, values:{열 이름: 값}}]}  (첫 시트 · 빈 행 제외)
// POST {key, updates:[{row, values:{열 이름: 값}, expect:{제목, 콘텐츠키}}]} → {ok, written, mismatched}  (없는 열은 머리줄 끝에 추가)

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function authorized_(key) {
  var saved = PropertiesService.getScriptProperties().getProperty('PRISM_KEY');
  return Boolean(saved) && typeof key === 'string' && key === saved;
}

var INPUT_COLS = ['콘텐츠키', '서비스', '제목', '본문', '원문 링크'];
var RESULT_COLS = ['리드문', '엔티티', '인텐트', '카테고리', '핵심키워드', '핵심문장', '모델', '처리 시각', '상태', '프리즘 해시', '실험 ID', '오류'];
var STATES = ['미검수', '실패', '검수 완료', '반영됨'];

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
    .addItem('연결 코드 보기', 'connectCode')
    .addToUi();
}

function setupMenu() {
  setup();
  SpreadsheetApp.getUi().alert('프리즘 양식을 만들었습니다. 입력 열을 채운 뒤 웹앱으로 배포하고 연결 코드를 확인하세요.');
}

// 연결 코드 = 웹앱 주소#비밀 키 · 키가 없으면 새로 만들어 스크립트 속성에 저장
function connectCode() {
  var ui = SpreadsheetApp.getUi();
  var url = ScriptApp.getService().getUrl() || '';
  var props = PropertiesService.getScriptProperties();
  var key = props.getProperty('PRISM_KEY');
  if (!key) {
    key = (Utilities.getUuid() + Utilities.getUuid()).replace(/-/g, '');
    props.setProperty('PRISM_KEY', key);
  }
  if (!/\/exec$/.test(url)) {
    ui.alert('비밀 키', '#' + key + '\n\n웹앱 주소를 찾지 못했습니다. 배포하지 않았다면 먼저 배포하세요(웹 앱 · 나로 실행 · 링크가 있는 모든 사용자). ' +
      '배포 관리에서 웹앱 URL(/exec)을 복사해 #키 앞에 붙이면 연결 코드가 됩니다.', ui.ButtonSet.OK);
    return;
  }
  ui.alert('연결 코드', url + '#' + key + '\n\n프리즘 키워드 실험실 STEP 3 연결 코드 칸에 붙여 넣으세요. 다른 사람에게 보이지 않게 다루세요.', ui.ButtonSet.OK);
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

function doGet(e) {
  if (!authorized_(e && e.parameter && e.parameter.key)) return json_({ok: false, error: 'unauthorized'});
  var sh = sheet_();
  var head = headers_(sh);
  if (head.indexOf('상태') < 0 || (head.indexOf('제목') < 0 && head.indexOf('본문') < 0)) {
    return json_({ok: false, error: '양식이 다릅니다 · setup 을 실행하세요'});
  }
  var lastRow = sh.getLastRow();
  var rows = [];
  if (lastRow >= 2 && head.length) {
    var data = sh.getRange(2, 1, lastRow - 1, head.length).getDisplayValues();
    for (var i = 0; i < data.length; i++) {
      var values = {}, filled = false;
      for (var c = 0; c < head.length; c++) {
        if (!head[c]) continue;
        values[head[c]] = data[i][c];
        if (data[i][c] !== '') filled = true;
      }
      if (filled) rows.push({row: i + 2, values: values});
    }
  }
  return json_({ok: true, rows: rows});
}

// 모델 결과가 = + - @ 로 시작하면 수식으로 해석되지 않게 글자로 적는다
function safe_(v) {
  var s = v == null ? '' : String(v);
  return /^[=+\-@]/.test(s) ? "'" + s : s;
}

// 읽을 때와 같은 행인지 확인(정렬·행 삽입으로 위치가 바뀌면 다른 콘텐츠에 덮어쓰지 않게) · 빈 값끼리는 일치
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

function doPost(e) {
  var body;
  try { body = JSON.parse(e.postData.contents); } catch (err) { return json_({ok: false, error: 'bad_json'}); }
  if (!authorized_(body.key)) return json_({ok: false, error: 'unauthorized'});
  var updates = Array.isArray(body.updates) ? body.updates : [];
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return json_({ok: false, error: 'busy'});
  try {
    var sh = sheet_();
    var head = headers_(sh);
    var written = 0, mismatched = [];
    for (var i = 0; i < updates.length; i++) {
      var u = updates[i] || {};
      var row = Number(u.row);
      if (!(row >= 2) || Math.floor(row) !== row || !u.values) continue;
      if (!sameRow_(sh, head, row, u.expect)) { mismatched.push(row); continue; }
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
    }
    SpreadsheetApp.flush();
    return json_({ok: true, written: written, mismatched: mismatched});
  } finally {
    lock.releaseLock();
  }
}
```

## 부록: 템플릿 없이 직접 만들기

템플릿 사본 버튼이 꺼져 있으면(템플릿 준비 중) 아래처럼 직접 만든다.

1. 새 구글 시트를 만들고 **확장 프로그램 › Apps Script** 를 연다. `Code.gs` 내용을 위 코드로 바꿔 저장한다.
2. 시트 탭을 새로고침하면 메뉴 **프리즘**이 생긴다. 이후는 설정 절차 2단계부터 같다.

템플릿 원본을 만드는 사람은 위 1번까지만 하고 **연결 코드 보기**를 누르지 않는다(원본에 비밀 키가 생기지 않게).
공유 링크 끝의 `/edit…` 를 `/copy` 로 바꾼 주소가 템플릿 사본 링크다.

## 보안

- 비밀 키는 Apps Script 스크립트 속성과 프리즘 팀 설정에만 둔다 · 프리즘 화면·응답에는 키 저장 여부만 보인다
- 웹앱은 비밀 키가 맞을 때만 읽고 쓴다 · 키가 새면 스크립트 속성 `PRISM_KEY` 와 프리즘 설정을 함께 바꾼다
- 웹앱은 시트 소유자 권한으로 실행되므로 다른 사람에게 Apps Script 편집 권한을 주지 않는다
