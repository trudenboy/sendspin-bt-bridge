import { config } from '@vue/test-utils'

// Menus, dialogs and drawers teleport to <body>; render them in place so a
// component test can find what it opened.
config.global.stubs = { ...config.global.stubs, teleport: true }

// Existing component tests assert the technical chips and rows, so they run
// with "Show technical details" on; tests of the hidden state turn it off.
import { beforeEach } from 'vitest'
import { setTechnicalDetails } from '@/composables/useTechnicalDetails'
beforeEach(() => setTechnicalDetails(true))
