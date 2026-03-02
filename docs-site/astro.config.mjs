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
        root: { label: 'Русский', lang: 'ru' },
        en: { label: 'English', lang: 'en' },
      },
      defaultLocale: 'root',
      editLink: {
        baseUrl: 'https://github.com/trudenboy/sendspin-bt-bridge/edit/main/docs-site/src/content/docs/',
      },
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/trudenboy/sendspin-bt-bridge' },
      ],
      sidebar: [
        { label: 'Главная', translations: { en: 'Home' }, link: '/' },
        {
          label: 'Установка', translations: { en: 'Installation' },
          autogenerate: { directory: 'installation' },
        },
        { label: 'Настройка', translations: { en: 'Configuration' }, slug: 'configuration' },
        { label: 'Веб-интерфейс', translations: { en: 'Web UI' }, slug: 'web-ui' },
        { label: 'Устройства', translations: { en: 'Devices' }, slug: 'devices' },
        { label: 'API Reference', slug: 'api' },
        { label: 'Устранение неполадок', translations: { en: 'Troubleshooting' }, slug: 'troubleshooting' },
        { label: 'Разработка', translations: { en: 'Contributing' }, slug: 'contributing' },
      ],
    }),
  ],
});
