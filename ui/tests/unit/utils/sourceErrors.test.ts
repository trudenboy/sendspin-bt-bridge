import { describe, it, expect } from 'vitest'
import { describeSourceCode } from '@/utils/sourceErrors'

/** The operator text is i18n; the mapper's job is the key pair, not the sentence. */
const t = (key: string) => key

describe('describeSourceCode', () => {
  it('gives the failed A2DP restoration its own explanation and next step', () => {
    expect(describeSourceCode('headset_a2dp_restore_failed', t)).toEqual({
      code: 'headset_a2dp_restore_failed',
      text: 'hardware.source.errorHeadsetA2dp',
      next: 'hardware.source.errorHeadsetA2dpNext',
    })
  })

  it('keeps the existing Bluetooth pairing codes unchanged', () => {
    expect(describeSourceCode('no_agent', t)).toEqual({
      code: 'no_agent',
      text: 'hardware.source.errorNoAgent',
      next: 'hardware.source.errorBluetoothNext',
    })
  })

  it('shows an unknown code verbatim with the generic next step', () => {
    expect(describeSourceCode('something_new', t)).toEqual({
      code: 'something_new',
      text: 'something_new',
      next: 'hardware.source.errorNextStep',
    })
  })
})
