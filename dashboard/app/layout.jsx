import "./globals.css";

export const metadata = {
  title: "NBA Bot Dashboard",
  description: "Engagement stats and posting-time intelligence for the NBA news bot",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
