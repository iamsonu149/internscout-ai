"use strict";

const MAX_BYTES = 10 * 1024 * 1024; // 10 MB local check (generous; only text goes to server)
const MAX_TEXT = 12000;

const upload = document.getElementById("workspace-resume-upload");
const status = document.getElementById("upload-status");

async function extractPdfText(file) {
  const pdfjsLib = await import("https://cdn.jsdelivr.net/npm/pdfjs-dist@4.9.155/build/pdf.min.mjs");
  pdfjsLib.GlobalWorkerOptions.workerSrc =
    "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.9.155/build/pdf.worker.min.mjs";
  const arrayBuffer = await file.arrayBuffer();
  const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;
  let text = "";
  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    const content = await page.getTextContent();
    text += content.items.map((item) => item.str).join(" ") + "\n";
    if (text.length >= MAX_TEXT) break;
  }
  return text.trim().slice(0, MAX_TEXT);
}

upload.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = upload.elements.resume.files[0];
  if (!file || file.size === 0 || file.size > MAX_BYTES) {
    status.textContent = "Choose a non-empty PDF (max 10 MB).";
    return;
  }
  if (!file.name.toLowerCase().endsWith(".pdf") && file.type !== "application/pdf") {
    status.textContent = "Only PDF files are supported.";
    return;
  }
  const btn = upload.querySelector("button");
  btn.disabled = true;
  status.textContent = "Extracting text from your resume…";
  try {
    const text = await extractPdfText(file);
    if (!text) {
      status.textContent =
        "No extractable text found. Scanned PDFs need OCR; upload a PDF with selectable text.";
      return;
    }
    status.textContent = "Sending to AI for parsing…";
    // Write extracted text into the hidden field, then do a plain form submit
    document.getElementById("resume-text-field").value = text;
    // Remove the file input so the form doesn't try to send binary data
    const fileInput = upload.elements.resume;
    fileInput.removeAttribute("required");
    fileInput.disabled = true;
    upload.submit();
  } catch (err) {
    console.error(err);
    status.textContent =
      "Could not read the PDF in your browser. Try a different PDF or export a fresh copy.";
  } finally {
    btn.disabled = false;
  }
});

window.addEventListener("pageshow", () => {
  upload.querySelector("button").disabled = false;
  status.textContent = "";
  const fileInput = upload.elements.resume;
  fileInput.disabled = false;
  fileInput.setAttribute("required", "");
  document.getElementById("resume-text-field").value = "";
});
