import axios from 'axios';

export const apiClient = axios.create({
  baseURL: '/api',
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    // Explicitly pass 401 errors down to the components.
    // We no longer trigger a blanket redirect to /signin.
    return Promise.reject(error);
  }
);
