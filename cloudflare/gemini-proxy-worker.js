/**
 * Прокси для Google Gemini API на Cloudflare Workers.
 *
 * Зачем: Google блокирует запросы к Gemini с IP-адресов дата-центров
 * Render (403 Forbidden). IP Cloudflare он не блокирует, поэтому бот
 * шлёт запросы сюда, а этот worker пересылает их Google как есть.
 *
 * Как развернуть (один раз, в браузере):
 * 1. dash.cloudflare.com → Workers & Pages → Create → Worker
 * 2. Имя: gemini-proxy → Deploy
 * 3. Edit code → заменить всё содержимое этим файлом → Deploy
 * 4. Скопировать адрес вида https://gemini-proxy.<аккаунт>.workers.dev
 * 5. На Render добавить переменную GEMINI_BASE_URL с этим адресом
 *
 * Ключ API остаётся в заголовках запроса от бота — worker его
 * не хранит и не видит в настройках.
 */

export default {
  async fetch(request) {
    const url = new URL(request.url);

    // Пересылаем путь и параметры без изменений
    const target =
      "https://generativelanguage.googleapis.com" + url.pathname + url.search;

    return fetch(target, {
      method: request.method,
      headers: request.headers,
      body: request.body,
    });
  },
};
