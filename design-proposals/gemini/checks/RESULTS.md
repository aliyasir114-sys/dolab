# نتائج فحص نظام التصميم / Design system check results

**آخر تشغيل:** 2026-11-20 · **المتصفح:** Google Chrome for Testing 153 بلا واجهة، عرض 420 بكسل · **الأداة:** `checks/check_showcase.py` (Playwright 1.63)

```bash
cd design-proposals/gemini/checks
python check_showcase.py ../showcase.html --chrome /usr/bin/google-chrome                       # أشرطة تمرير عائمة (الهاتف، macOS)
python check_showcase.py ../showcase.html --chrome /usr/bin/google-chrome --classic-scrollbars  # أشرطة تأخذ مساحة (Windows)
```

## الجولة الثانية: ملاحظات مراجعة Copilot (2026-11-20)

| الإصدار | الفحوص | ناجح | فاشل |
|---|---|---|---|
| قبل هذه الجولة (22ac36f)، بالفحوص الموسّعة | 138 | 108 | **30** |
| بعد الإصلاح، أشرطة تمرير عائمة | 138 | **138** | 0 |
| بعد الإصلاح، أشرطة تمرير تقليدية | 138 | **138** | 0 |

الفحوص الجديدة كُتبت أولاً وشُغّلت على الإصدار السابق فأظهرت الملاحظات الخمس كلها (30 فشلاً)، ثم أُصلحت. لذلك نجاحها الآن دليل على الإصلاح، لا على أن الفحص لا يرى المشكلة. ومع ذلك فهي **فحوص آلية في محرك واحد**؛ انظر «ما لم يُختبر».

### نتيجة كل بند

| البند | الإصلاح | ما يثبته الفحص | النتيجة |
|---|---|---|---|
| **High: التركيز على الشريط الداكن** | قاعدة عامة في `theme.css`: `.surface-dark :focus-visible` يجعل لون الإطار `#F5F5F5`؛ الشريط يحمل `surface-dark`. يتغير اللون فقط | كل أزرار الشريط (زر واحد حالياً) يُوصَل إليها بـ Tab؛ إطار 2px متصل؛ تباينه مع الشريط **18.37:1**؛ الزر لا يتحرك ولا يتغير حجمه عند التركيز | ناجح في RTL وLTR |
| **Medium: تمرير الخلفية** | `html.is-scroll-locked { overflow: hidden }` عند الفتح؛ حفظ موضع التمرير واستعادته عند الإغلاق؛ إعادة التركيز بـ `preventScroll`؛ حجز مساحة الشريط فقط حين يأخذ مساحة | لكل طرق الإغلاق الأربع (Escape، زر ×، إلغاء، تأكيد): العجلة وPageDown وHome لا تحرك الخلفية؛ لا إزاحة في التخطيط؛ الموضع يعود كما كان؛ التمرير يعمل بعد الإغلاق | ناجح، بنوعي شريط التمرير |
| **Medium: اللغة الإنجليزية** | الوضع الإنجليزي يعرض نصوصاً إنجليزية فعلية لكل العينات والتسميات وaria-label وplaceholder؛ النصوص العربية تبقى كما كتبها Gemini؛ الأجزاء الإنجليزية داخل الصفحة العربية (عناوين الأقسام، «Primary»…) عليها `lang="en"` | لا نص عربي تحت لغة غير `ar`؛ كل نص إنجليزي تحت `lang="en"`؛ الصفحة الإنجليزية بلا أي نص عربي؛ رسالة الخطأ بلغة الصفحة | ناجح |
| **Low: رسالة الخطأ** | منطقة `role="alert"` فارغة عند التحميل، تُملأ عند فشل التحقق (عند مغادرة الحقل)، وتُفرَّغ عند تصحيح القيمة؛ `aria-invalid` يتبع الحالة | لا خطأ عند التحميل؛ «abc» تُظهر الخطأ وتضبط `aria-invalid`؛ الخطأ مربوط بالحقل؛ «45» تزيله | ناجح |
| **Low: الخطوط** | `@font-face` يستخدم `local()` فقط إلى أن تُضاف الملفات؛ لا مراجع لملفات غير موجودة | لا أخطاء في Console ولا موارد فاشلة (كان هناك 5 طلبات `/fonts/*.woff2` فاشلة) | ناجح |

إضافة كشفها الفحص: فحص «الصفحة خلف النافذة معزولة» صار يقرأ **شجرة الوصول الفعلية في Chrome** (التي يقرؤها قارئ الشاشة)، لا وجود سمة `inert` فقط. أثناء فتح النافذة لا يظهر فيها إلا عنوان النافذة (`تأكيد الحذف` / `Confirm deletion`).

### التباين والهوية البصرية (لا تراجع)
- التباين كما هو: النص الرئيسي 18.37:1، الثانوي 5.95:1، نص المثال 4.68:1، حدود الحقول 3.38:1.
- لا لون جديد: الإطار على الشريط الداكن هو `--color-surface` (`#F5F5F5`) من اللوحة.
- لم تتغير المسافات أو الأحجام أو الخطوط أو الأشكال. لقطات الشاشة بالعربية والإنجليزية روجعت بالعين بعد الإصلاح.

### الخطوط: ما بقي مؤجلاً
- الخطان مرخّصان بـ SIL Open Font License 1.1: IBM Plex Sans Arabic (‏IBM، اسم «Plex» محجوز) وSpace Grotesk.
- لم تُضَف ملفات الخطوط. تُضاف في الخطوة F من مصادرها الرسمية دون تعديل مع `OFL.txt`، بمسار نسبي لا `/fonts/`. حتى ذلك الحين يظهر خط الجهاز إن كان مثبتاً، وإلا الخط الاحتياطي.

### ما لم يُختبر
- **قارئات شاشة حقيقية** (NVDA، JAWS، VoiceOver، TalkBack): فُحصت شجرة الوصول في Chrome فقط، لا ما يُقرأ فعلاً ولا توقيت إعلان رسالة الخطأ.
- **متصفحات أخرى:** Firefox وSafari (منها قفل التمرير باللمس في iOS، حيث `overflow: hidden` كان غير كافٍ في إصدارات قديمة).
- **أجهزة اللمس** والتمرير باللمس، والتكبير 200%، ووضع التباين العالي في Windows، وتفضيل تقليل الحركة.
- **الخطوط الحقيقية:** الفحص جرى بالخط الاحتياطي؛ أطوال الأسطر قد تختلف بعد إضافة الملفات.
- عنوان المستند (`<title>`) يبقى إنجليزياً في الوضعين.

## الجولة الأولى: إصلاحات التكامل والوصول (2026-11-20)
ملفات Gemini كما سُلِّمت: 27 من 54 فحصاً. بعد الإصلاحات (اللوحة، السهم، طبقة النافذة، عزلها، التركيز على الحقل الخاطئ، التباين): 54 من 54. التفاصيل في وصف طلب الدمج #25 وفي commit ‏22ac36f.

## النتيجة الكاملة لآخر تشغيل (أشرطة عائمة)

```
PASS  RTL  dir and lang
PASS  RTL  font family  ["IBM Plex Sans Arabic", sans-serif]
PASS  RTL  no Arabic text is marked as another language
PASS  RTL  English text is marked lang=en
PASS  RTL  component text is in the page language  [حفظ القطعة (Primary)]
PASS  RTL  forward arrow points left  [matrix(-1, 0, 0, 1, 0, 0)]
PASS  RTL  .card uses the palette white, not #FFFFFF  [(245, 245, 245)]
PASS  RTL  .input-field uses the palette white, not #FFFFFF  [(245, 245, 245)]
PASS  RTL  .modal-content uses the palette white, not #FFFFFF  [(245, 245, 245)]
PASS  RTL  main text contrast >= 4.5  [18.37]
PASS  RTL  secondary text contrast >= 4.5  [5.95]
PASS  RTL  placeholder contrast >= 4.5  [4.68]
PASS  RTL  field border contrast >= 3  [3.38]
PASS  RTL  disabled link is skipped by Tab
PASS  RTL  closed dialog is not reachable
PASS  RTL  disabled link ignores the pointer
PASS  RTL  keyboard focus shows an outline on page buttons
PASS  RTL  toolbar button 1 is reached by Tab
PASS  RTL  toolbar button 1 focus outline is visible on the dark bar (2px, >= 3:1)  [solid 2px rgb(245, 245, 245), 18.37:1]
PASS  RTL  toolbar button 1 keeps its size and place on focus
PASS  RTL  no error shown on load
PASS  RTL  error region is announced to screen readers (role=alert or aria-live)
PASS  RTL  an invalid value shows the error  [يرجى إدخال أرقام صحيحة فقط.]
PASS  RTL  the error is linked to its field (aria-describedby)
PASS  RTL  the error is in the page language  [يرجى إدخال أرقام صحيحة فقط.]
PASS  RTL  focus is visible on the invalid field  [rgb(126, 126, 126) none solid 2px]
PASS  RTL  focus is visible on a normal field  [rgb(133, 133, 133) rgb(8, 8, 8) 0px 0px 0px 1px inset none 3px]
PASS  RTL  a valid value clears the error
PASS  RTL  dialog opens [Escape]
PASS  RTL  focus moves into the dialog [Escape]
PASS  RTL  background does not scroll while open [Escape]  [567 -> 567]
PASS  RTL  locking the scroll does not shift the layout [Escape]  [x 0->0, width 420->420]
PASS  RTL  page behind is inert
PASS  RTL  screen readers reach only the dialog while it is open (Chrome accessibility tree)  [headings: ['تأكيد الحذف']]
PASS  RTL  dialog backdrop covers the sticky toolbar
PASS  RTL  toolbar button cannot be used while open
PASS  RTL  Tab and Shift+Tab stay inside the dialog  [[True, True, True, True, True, True, True, True]]
PASS  RTL  focus stays inside after clicking the backdrop
PASS  RTL  focus cannot be moved to the page by script while open
PASS  RTL  dialog closes [Escape]
PASS  RTL  scroll position is restored [Escape]  [567 -> 567]
PASS  RTL  page is interactive and scrollable again [Escape]
PASS  RTL  focus returns to the opening button [Escape]
PASS  RTL  dialog opens [.modal-close]
PASS  RTL  focus moves into the dialog [.modal-close]
PASS  RTL  background does not scroll while open [.modal-close]  [567 -> 567]
PASS  RTL  locking the scroll does not shift the layout [.modal-close]  [x 0->0, width 420->420]
PASS  RTL  dialog closes [.modal-close]
PASS  RTL  scroll position is restored [.modal-close]  [567 -> 567]
PASS  RTL  page is interactive and scrollable again [.modal-close]
PASS  RTL  focus returns to the opening button [.modal-close]
PASS  RTL  dialog opens [.modal-footer .btn-secondary]
PASS  RTL  focus moves into the dialog [.modal-footer .btn-secondary]
PASS  RTL  background does not scroll while open [.modal-footer .btn-secondary]  [567 -> 567]
PASS  RTL  locking the scroll does not shift the layout [.modal-footer .btn-secondary]  [x 0->0, width 420->420]
PASS  RTL  dialog closes [.modal-footer .btn-secondary]
PASS  RTL  scroll position is restored [.modal-footer .btn-secondary]  [567 -> 567]
PASS  RTL  page is interactive and scrollable again [.modal-footer .btn-secondary]
PASS  RTL  focus returns to the opening button [.modal-footer .btn-secondary]
PASS  RTL  dialog opens [.modal-footer .btn-primary]
PASS  RTL  focus moves into the dialog [.modal-footer .btn-primary]
PASS  RTL  background does not scroll while open [.modal-footer .btn-primary]  [567 -> 567]
PASS  RTL  locking the scroll does not shift the layout [.modal-footer .btn-primary]  [x 0->0, width 420->420]
PASS  RTL  dialog closes [.modal-footer .btn-primary]
PASS  RTL  scroll position is restored [.modal-footer .btn-primary]  [567 -> 567]
PASS  RTL  page is interactive and scrollable again [.modal-footer .btn-primary]
PASS  RTL  focus returns to the opening button [.modal-footer .btn-primary]
PASS  RTL  the page scrolls normally after the dialog closes
PASS  LTR  dir and lang
PASS  LTR  font family  ["Space Grotesk", sans-serif]
PASS  LTR  no Arabic text is marked as another language
PASS  LTR  English text is marked lang=en
PASS  LTR  English page shows English content (no Arabic left)
PASS  LTR  component text is in the page language  [Save item (Primary)]
PASS  LTR  forward arrow points right  [none]
PASS  LTR  .card uses the palette white, not #FFFFFF  [(245, 245, 245)]
PASS  LTR  .input-field uses the palette white, not #FFFFFF  [(245, 245, 245)]
PASS  LTR  .modal-content uses the palette white, not #FFFFFF  [(245, 245, 245)]
PASS  LTR  main text contrast >= 4.5  [18.37]
PASS  LTR  secondary text contrast >= 4.5  [5.95]
PASS  LTR  placeholder contrast >= 4.5  [4.68]
PASS  LTR  field border contrast >= 3  [3.38]
PASS  LTR  disabled link is skipped by Tab
PASS  LTR  closed dialog is not reachable
PASS  LTR  disabled link ignores the pointer
PASS  LTR  keyboard focus shows an outline on page buttons
PASS  LTR  toolbar button 1 is reached by Tab
PASS  LTR  toolbar button 1 focus outline is visible on the dark bar (2px, >= 3:1)  [solid 2px rgb(245, 245, 245), 18.37:1]
PASS  LTR  toolbar button 1 keeps its size and place on focus
PASS  LTR  no error shown on load
PASS  LTR  error region is announced to screen readers (role=alert or aria-live)
PASS  LTR  an invalid value shows the error  [Please enter numbers only.]
PASS  LTR  the error is linked to its field (aria-describedby)
PASS  LTR  the error is in the page language  [Please enter numbers only.]
PASS  LTR  focus is visible on the invalid field  [rgb(131, 131, 131) none solid 2px]
PASS  LTR  focus is visible on a normal field  [rgb(133, 133, 133) rgb(8, 8, 8) 0px 0px 0px 1px inset none 3px]
PASS  LTR  a valid value clears the error
PASS  LTR  dialog opens [Escape]
PASS  LTR  focus moves into the dialog [Escape]
PASS  LTR  background does not scroll while open [Escape]  [498 -> 498]
PASS  LTR  locking the scroll does not shift the layout [Escape]  [x 0->0, width 420->420]
PASS  LTR  page behind is inert
PASS  LTR  screen readers reach only the dialog while it is open (Chrome accessibility tree)  [headings: ['Confirm deletion']]
PASS  LTR  dialog backdrop covers the sticky toolbar
PASS  LTR  toolbar button cannot be used while open
PASS  LTR  Tab and Shift+Tab stay inside the dialog  [[True, True, True, True, True, True, True, True]]
PASS  LTR  focus stays inside after clicking the backdrop
PASS  LTR  focus cannot be moved to the page by script while open
PASS  LTR  dialog closes [Escape]
PASS  LTR  scroll position is restored [Escape]  [498 -> 498]
PASS  LTR  page is interactive and scrollable again [Escape]
PASS  LTR  focus returns to the opening button [Escape]
PASS  LTR  dialog opens [.modal-close]
PASS  LTR  focus moves into the dialog [.modal-close]
PASS  LTR  background does not scroll while open [.modal-close]  [498 -> 498]
PASS  LTR  locking the scroll does not shift the layout [.modal-close]  [x 0->0, width 420->420]
PASS  LTR  dialog closes [.modal-close]
PASS  LTR  scroll position is restored [.modal-close]  [498 -> 498]
PASS  LTR  page is interactive and scrollable again [.modal-close]
PASS  LTR  focus returns to the opening button [.modal-close]
PASS  LTR  dialog opens [.modal-footer .btn-secondary]
PASS  LTR  focus moves into the dialog [.modal-footer .btn-secondary]
PASS  LTR  background does not scroll while open [.modal-footer .btn-secondary]  [498 -> 498]
PASS  LTR  locking the scroll does not shift the layout [.modal-footer .btn-secondary]  [x 0->0, width 420->420]
PASS  LTR  dialog closes [.modal-footer .btn-secondary]
PASS  LTR  scroll position is restored [.modal-footer .btn-secondary]  [498 -> 498]
PASS  LTR  page is interactive and scrollable again [.modal-footer .btn-secondary]
PASS  LTR  focus returns to the opening button [.modal-footer .btn-secondary]
PASS  LTR  dialog opens [.modal-footer .btn-primary]
PASS  LTR  focus moves into the dialog [.modal-footer .btn-primary]
PASS  LTR  background does not scroll while open [.modal-footer .btn-primary]  [498 -> 498]
PASS  LTR  locking the scroll does not shift the layout [.modal-footer .btn-primary]  [x 0->0, width 420->420]
PASS  LTR  dialog closes [.modal-footer .btn-primary]
PASS  LTR  scroll position is restored [.modal-footer .btn-primary]  [498 -> 498]
PASS  LTR  page is interactive and scrollable again [.modal-footer .btn-primary]
PASS  LTR  focus returns to the opening button [.modal-footer .btn-primary]
PASS  LTR  the page scrolls normally after the dialog closes
PASS  BOTH  no console errors, script errors or failed resource loads
138 passed, 0 failed (overlay scrollbars)
```
