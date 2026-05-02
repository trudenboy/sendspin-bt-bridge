// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import remarkMermaid from './src/plugins/remark-mermaid.mjs';

export default defineConfig({
  site: 'https://trudenboy.github.io/sendspin-bt-bridge',
  base: '/sendspin-bt-bridge',
  markdown: {
    remarkPlugins: [remarkMermaid],
  },
  integrations: [
    starlight({
      title: 'Sendspin BT Bridge',
      components: {
        Head: './src/components/Head.astro',
      },
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
        { label: 'Bluetooth Adapters', translations: { ru: 'Bluetooth-адаптеры' }, slug: 'bluetooth-adapters' },
        { label: 'API Reference', slug: 'api' },
        { label: 'Architecture', slug: 'architecture' },
        { label: 'Troubleshooting', translations: { ru: 'Устранение неполадок' }, slug: 'troubleshooting' },
        { label: 'Test Stand', translations: { ru: 'Тестовый стенд' }, slug: 'test-stand' },
        { label: 'Contributing', translations: { ru: 'Разработка' }, slug: 'contributing' },
        { label: 'Sponsor the project', translations: { ru: 'Поддержать проект' }, slug: 'support' },
        { label: 'Project Stats', translations: { ru: 'Статистика проекта' }, link: '/stats/' },
        {
          label: 'Journey log',
          translations: { ru: 'Бортжурнал' },
          autogenerate: { directory: 'journey-log' },
          collapsed: true,
        },
      ],
    }),
  ],
});
