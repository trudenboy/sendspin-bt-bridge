import { config } from '@vue/test-utils'

// Menus, dialogs and drawers teleport to <body>; render them in place so a
// component test can find what it opened.
config.global.stubs = { ...config.global.stubs, teleport: true }
