import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./styles.css";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/ui";
import { getSession } from "./lib/api";
import Login from "./pages/Login";
import Today from "./pages/Today";
import Payments from "./pages/Payments";
import NewPayment from "./pages/NewPayment";
import Review from "./pages/Review";
import Receiving from "./pages/Receiving";
import Returns from "./pages/Returns";
import Files from "./pages/Files";
import Originators from "./pages/Originators";
import CalendarPage from "./pages/Calendar";
import Incidents from "./pages/Incidents";
import Audit from "./pages/Audit";

function Guard({ children }: { children: React.ReactNode }) {
  return getSession() ? <>{children}</> : <Navigate to="/login" replace />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ToastProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<Guard><Layout /></Guard>}>
            <Route index element={<Today />} />
            <Route path="review" element={<Review />} />
            <Route path="payments" element={<Payments />} />
            <Route path="payments/new" element={<NewPayment />} />
            <Route path="originators" element={<Originators />} />
            <Route path="receiving" element={<Receiving />} />
            <Route path="returns" element={<Returns />} />
            <Route path="files" element={<Files />} />
            <Route path="calendar" element={<CalendarPage />} />
            <Route path="incidents" element={<Incidents />} />
            <Route path="audit" element={<Audit />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </ToastProvider>
  </React.StrictMode>,
);
