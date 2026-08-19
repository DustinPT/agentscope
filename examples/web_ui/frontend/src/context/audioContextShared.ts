import { createContext } from 'react';

import { StreamingAudioManager } from '@/utils/streamingAudio';

export interface ReplayController {
        play: (el: HTMLAudioElement) => void;
        stop: () => void;
}

export const AudioContext = createContext<StreamingAudioManager | null>(null);
export const ReplayContext = createContext<ReplayController | null>(null);
