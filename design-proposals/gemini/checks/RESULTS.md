# نتائج فحص نظام التصميم / Design system check results

**التاريخ:** 2026-11-20 · **المتصفح:** Google Chrome بلا واجهة، عرض 420 بكسل · **الأداة:** `checks/check_showcase.py` (Playwright)

```bash
cd design-proposals/gemini/checks
python check_showcase.py ../showcase.html --chrome /usr/bin/google-chrome --shots shots/
```

| النسخة | ناجح | فاشل |
|---|---|---|
| ملفات Gemini كما سُلِّمت | 27 | 27 |
| بعد إصلاحات التكامل والوصول (توجيهات Ali، 2026-11-20) | **54** | **0** |

كل فحص يعمل مرتين: بالعربية (RTL) ثم بالإنجليزية (LTR). التباين يُقاس من الألوان التي يرسمها المتصفح فعلاً (WCAG 2.1).

## الإصلاحات

| المشكلة | الإصلاح | الملف |
|---|---|---|
| الأبيض الصافي `#FFFFFF` للأسطح | `--color-surface: #F5F5F5` (أبيض اللوحة) | `theme.css` |
| سهم «متابعة» معكوس في اللغتين | الانعكاس في `[dir="rtl"]` بدل `[dir="ltr"]` | `theme.css` |
| الشريط العلوي فوق النافذة المنبثقة | `--z-modal: 1000` للنافذة | `theme.css` |
| لا تركيز مرئي على الحقل الخاطئ | إطار خارجي 2px عند التركيز؛ والحقل العادي يصبح حده أسود بسمك 2px دون تحريك التخطيط | `theme.css` |
| نص المثال 2.38:1 | `#6E6E6E` ← 4.68:1 | `theme.css` |
| حدود الحقول 1.29:1 | رمز جديد `--color-field-border: #858585` ← 3.38:1؛ `--color-border` يبقى للفواصل والبطاقات | `theme.css` |
| نص الزر المعطّل كان يستعمل لون نص المثال | رمز مستقل `--color-text-disabled: #A8A8A8` (العناصر المعطّلة مستثناة من شرط التباين) | `theme.css` |
| الصفحة خلف النافذة مقروءة وقابلة للتفاعل | `inert` على الصفحة أثناء فتح النافذة، ويُزال قبل إعادة التركيز | `showcase.html` |
| التركيز يخرج بعد الضغط على الخلفية | التركيز الخارج يعود إلى النافذة (`focusin` وTab) | `showcase.html` |

لم يتغير شيء آخر: المسافات والخطوط والأحجام والحواف الحادة وشكل المكونات كما صممها Gemini.

## ما لم يُعالَج هنا (خارج نطاق التوجيهات)
- ملفات الخطوط غير مرفقة؛ المتصفح يعرض الخط الاحتياطي.
- أسماء الأصناف العامة (`.btn`، `.card`) والقواعد العامة على `body` و`*`.
- `role="alert"` على رسالة الخطأ منذ تحميل الصفحة.

## النتيجة الكاملة بعد الإصلاح

```
PASS  RTL  dir and lang
PASS  RTL  font family  ["IBM Plex Sans Arabic", sans-serif]
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
PASS  RTL  keyboard focus shows an outline
PASS  RTL  disabled link ignores the pointer
PASS  RTL  focus is visible on #itemName  [rgb(133, 133, 133) rgb(8, 8, 8) 0px 0px 0px 1px inset none 3px]
PASS  RTL  focus is visible on #itemPrice (invalid)  [rgb(8, 8, 8) none solid 2px]
PASS  RTL  dialog opens
PASS  RTL  focus moves into the dialog
PASS  RTL  page behind is inert (no clicks, no focus, hidden from screen readers)
PASS  RTL  dialog backdrop covers the sticky toolbar
PASS  RTL  toolbar button cannot be used while the dialog is open
PASS  RTL  Tab and Shift+Tab stay inside the dialog  [[True, True, True, True, True, True, True, True]]
PASS  RTL  focus stays inside after clicking the backdrop
PASS  RTL  focus cannot be moved to the page by script while open
PASS  RTL  Escape closes the dialog
PASS  RTL  page is interactive again
PASS  RTL  focus returns to the opening button
PASS  LTR  dir and lang
PASS  LTR  font family  ["Space Grotesk", sans-serif]
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
PASS  LTR  keyboard focus shows an outline
PASS  LTR  disabled link ignores the pointer
PASS  LTR  focus is visible on #itemName  [rgb(133, 133, 133) rgb(8, 8, 8) 0px 0px 0px 1px inset none 3px]
PASS  LTR  focus is visible on #itemPrice (invalid)  [rgb(8, 8, 8) none solid 2px]
PASS  LTR  dialog opens
PASS  LTR  focus moves into the dialog
PASS  LTR  page behind is inert (no clicks, no focus, hidden from screen readers)
PASS  LTR  dialog backdrop covers the sticky toolbar
PASS  LTR  toolbar button cannot be used while the dialog is open
PASS  LTR  Tab and Shift+Tab stay inside the dialog  [[True, True, True, True, True, True, True, True]]
PASS  LTR  focus stays inside after clicking the backdrop
PASS  LTR  focus cannot be moved to the page by script while open
PASS  LTR  Escape closes the dialog
PASS  LTR  page is interactive again
PASS  LTR  focus returns to the opening button
54 passed, 0 failed
```
