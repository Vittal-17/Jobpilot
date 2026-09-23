import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Layout } from '@/components/layout/Layout';
import { Today } from '@/pages/Today';
import { Login } from '@/pages/Login';
import { Feed } from '@/pages/Feed';
import { Search } from '@/pages/Search';
import { System } from '@/pages/System';
import { Settings } from '@/pages/Settings';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60 * 1000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Layout><Today /></Layout>} />
          <Route path="/jobs" element={<Layout><Feed /></Layout>} />
          <Route path="/search" element={<Layout><Search /></Layout>} />
          <Route path="/system" element={<Layout><System /></Layout>} />
          <Route path="/settings" element={<Layout><Settings /></Layout>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
