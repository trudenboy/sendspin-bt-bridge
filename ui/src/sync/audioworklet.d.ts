/**
 * The few AudioWorkletGlobalScope names the capture worklet uses. `@types/audioworklet`
 * is not a dependency, and the DOM lib does not describe the worklet scope.
 */
declare abstract class AudioWorkletProcessor {
  readonly port: MessagePort;
  constructor();
  abstract process(
    inputs: Float32Array[][],
    outputs: Float32Array[][],
    parameters: Record<string, Float32Array>,
  ): boolean;
}

declare function registerProcessor(
  name: string,
  processorCtor: new () => AudioWorkletProcessor,
): void;

/** Frame index of the block being processed: the context's own sample clock. */
declare const currentFrame: number;
declare const sampleRate: number;
