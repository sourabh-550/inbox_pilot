from attachments import extract_attachment_text

text, unparsed = extract_attachment_text("mock_interview.pdf")
print("Extracted text:", repr(text))
print("Unparsed:", unparsed)