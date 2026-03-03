// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://trudenboy.github.io/sendspin-bt-bridge',
  base: '/sendspin-bt-bridge',
  integrations: [
    starlight({
      title: 'Sendspin BT Bridge',
      locales: {
        root: { label: 'English', lang: 'en' },
        ru: { label: 'Русский', lang: 'ru' },
      },
      defaultLocale: 'root',
      editLink: {
        baseUrl: 'https://github.com/trudenboy/sendspin-bt-bridge/edit/main/docs-site/src/content/docs/',
      },
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/trudenboy/sendspin-bt-bridge' },
      ],
      sidebar: [
        { label: 'Home', translations: { ru: 'Главная' }, link: '/' },
        {
          label: 'Installation', translations: { ru: 'Установка' },
          autogenerate: { directory: 'installation' },
        },
        { label: 'Configuration', translations: { ru: 'Настройка' }, slug: 'configuration' },
        { label: 'Web UI', translations: { ru: 'Веб-интерфейс' }, slug: 'web-ui' },
        { label: 'Devices', translations: { ru: 'Устройства' }, slug: 'devices' },
        { label: 'API Reference', slug: 'api' },
        { label: 'Troubleshooting', translations: { ru: 'Устранение неполадок' }, slug: 'troubleshooting' },
        { label: 'Contributing', translations: { ru: 'Разработка' }, slug: 'contributing' },
      ],
    }),
  ],
});
