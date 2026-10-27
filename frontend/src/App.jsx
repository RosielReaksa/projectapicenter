import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './lib/auth'
import Layout from './components/Layout'
import PublicLayout from './components/PublicLayout'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import KeysPage from './pages/KeysPage'
import ProvidersPage from './pages/ProvidersPage'
import ActivityPage from './pages/ActivityPage'
import UsagePage from './pages/UsagePage'
import ModelsPage from './pages/ModelsPage'
import RoutingPage from './pages/RoutingPage'
import PlaygroundPage from './pages/PlaygroundPage'
import PartnersPage from './pages/PartnersPage'
import SettingsPage from './pages/SettingsPage'
import AccessRequestsPage from './pages/AccessRequestsPage'
import MaintenancePage from './pages/MaintenancePage'
import DocsPage from './pages/DocsPage'
import HomePage from './pages/public/HomePage'
import PublicModelsPage from './pages/public/PublicModelsPage'
import PublicDocsPage from './pages/public/PublicDocsPage'
import RequestAccessPage from './pages/public/RequestAccessPage'
import { PrivacyPage, TermsPage } from './pages/public/LegalPages'
import { Spinner } from './components/ui'

function RequireAuth({ children }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <Spinner label="Checking session" />
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />
  return children
}

function RequireOwner({ children }) {
  const { isOwner } = useAuth()
  if (!isOwner) return <Navigate to="/console" replace />
  return children
}

export default function App() {
  return (
    <Routes>
      {/* ------------------------------------------------- public site */}
      <Route element={<PublicLayout />}>
        <Route index element={<HomePage />} />
        <Route path="models" element={<PublicModelsPage />} />
        <Route path="docs" element={<PublicDocsPage />} />
        <Route path="request-access" element={<RequestAccessPage />} />
        <Route path="terms" element={<TermsPage />} />
        <Route path="privacy" element={<PrivacyPage />} />
      </Route>

      <Route path="/login" element={<LoginPage />} />

      {/* ------------------------------------------------ console (auth) */}
      <Route
        path="/console"
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="keys" element={<KeysPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="usage" element={<UsagePage />} />
        <Route path="playground" element={<PlaygroundPage />} />
        <Route path="docs" element={<DocsPage />} />
        <Route
          path="providers"
          element={
            <RequireOwner>
              <ProvidersPage />
            </RequireOwner>
          }
        />
        <Route
          path="models"
          element={
            <RequireOwner>
              <ModelsPage />
            </RequireOwner>
          }
        />
        <Route
          path="routing"
          element={
            <RequireOwner>
              <RoutingPage />
            </RequireOwner>
          }
        />
        <Route
          path="partners"
          element={
            <RequireOwner>
              <PartnersPage />
            </RequireOwner>
          }
        />
        <Route
          path="access-requests"
          element={
            <RequireOwner>
              <AccessRequestsPage />
            </RequireOwner>
          }
        />
        <Route
          path="maintenance"
          element={
            <RequireOwner>
              <MaintenancePage />
            </RequireOwner>
          }
        />
        <Route
          path="settings"
          element={
            <RequireOwner>
              <SettingsPage />
            </RequireOwner>
          }
        />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
