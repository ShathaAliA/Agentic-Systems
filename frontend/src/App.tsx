import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Admin from './pages/Admin'
import Chat from './pages/Chat'
import Landing from './pages/Landing'
import Login from './pages/Login'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/login" element={<Login />} />
        <Route path="/app" element={<Chat />} />
        <Route path="/admin/*" element={<Admin />} />
        <Route path="*" element={<Landing />} />
      </Routes>
    </BrowserRouter>
  )
}
