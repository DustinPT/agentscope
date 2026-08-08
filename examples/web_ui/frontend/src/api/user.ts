import { client } from './client';
import type { UserModelDefaults } from './types';

export const userApi = {
        getModelDefaults: () => client.get<UserModelDefaults>('/user/model-defaults'),

        updateModelDefaults: (body: UserModelDefaults) =>
                client.put<UserModelDefaults>('/user/model-defaults', body),
};
