/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/renderer/index.html', './src/renderer/src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        mist: '#e7f2ec',
        sage: '#5f8f78',
        pool: '#d7e6f2',
        ink: '#243038',
      },
    },
  },
  plugins: [],
}
