# Routes

The application is Flask/Jinja, not file-routed React.

| URL | View | Template/layout |
| --- | --- | --- |
| `/` | Dashboard | `src/sendspin_bridge/web/templates/index.html` |
| `/login` | Authentication | `src/sendspin_bridge/web/templates/login.html` |
| `/logout` | Logout method helper | `src/sendspin_bridge/web/templates/logout_method.html` |

The dashboard device cards and list rows are rendered client-side by `src/sendspin_bridge/web/static/app.js` into the status grid. Styling is in `src/sendspin_bridge/web/static/style.css`.
