from pathlib import Path
import markdown

root=Path(__file__).resolve().parent.parent
source=(root/'docs/USER_MANUAL.md').read_text(encoding='utf-8')
body=markdown.markdown(source,extensions=['tables','fenced_code','toc'])
body=body.replace('src="screenshots/', 'src="/static/manual/')
template='''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Инструкция пользователя — БроньЛаб</title><link rel="icon" href="/static/logo.svg"><link rel="stylesheet" href="/static/help.css?v=20261019"></head><body><header><a href="/">← Вернуться в БроньЛаб</a><span>РУКОВОДСТВО ПОЛЬЗОВАТЕЛЯ</span></header><main>'''+body+'''</main><footer><a href="/">Перейти к бронированию →</a></footer></body></html>'''
(root/'bronylab/templates/help.html').write_text(template,encoding='utf-8')
print('Built help.html from docs/USER_MANUAL.md')
