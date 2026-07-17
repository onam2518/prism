/* Prism 앱 스크립트(Alpine) 로더 · 조각(app-NN-*.js)들이 window.PRISM_APP_PARTS 에
   등록한 프로퍼티 그룹을 디스크립터 병합(게터 보존)해 단일 prismApp 데이터로 합성한다.
   조각은 이 파일보다 먼저 로드돼야 한다(00-head.html 의 script 순서가 원천). */
  document.addEventListener('alpine:init', () => {
    Alpine.data('prismApp', () => {
      const app = {};
      for (const make of (window.PRISM_APP_PARTS || [])) {
        Object.defineProperties(app, Object.getOwnPropertyDescriptors(make()));
      }
      return app;
    });
  });
