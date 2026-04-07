"""
HTML-страница 404 для неизвестных маршрутов.
"""

NOT_FOUND_HTML = """\
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>404 — Страница не найдена</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f0f1a;
    color: #e0e0e0;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    text-align: center;
    padding: 20px;
  }
  .container { max-width: 480px; }
  .code {
    font-size: 120px;
    font-weight: 800;
    background: linear-gradient(135deg, #f97316, #ef4444);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    line-height: 1;
    margin-bottom: 16px;
  }
  h1 {
    font-size: 24px;
    font-weight: 600;
    margin-bottom: 12px;
    color: #fff;
  }
  p {
    font-size: 16px;
    color: #9ca3af;
    margin-bottom: 32px;
    line-height: 1.5;
  }
  .btn {
    display: inline-block;
    padding: 14px 32px;
    background: linear-gradient(135deg, #f97316, #ef4444);
    color: #fff;
    text-decoration: none;
    border-radius: 8px;
    font-size: 16px;
    font-weight: 600;
    transition: opacity 0.2s;
  }
  .btn:hover { opacity: 0.85; }
</style>
</head>
<body>
<div class="container">
  <div class="code">404</div>
  <h1>Страница не найдена</h1>
  <p>Такой страницы не существует. Возможно, вы перешли по устаревшей ссылке.</p>
  <a class="btn" href="https://viktorelenich.github.io/treiner-web/">Перейти на сайт тренера</a>
</div>
</body>
</html>
"""
