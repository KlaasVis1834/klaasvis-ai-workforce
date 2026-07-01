document.addEventListener("DOMContentLoaded", () => {
    const attachmentSelect = document.querySelector('select[name="has_attachments"]');
    const attachmentInput = document.querySelector('input[name="attachment_names"]');

    if (!attachmentSelect || !attachmentInput) {
        return;
    }

    const syncAttachmentInput = () => {
        attachmentInput.disabled = attachmentSelect.value !== "yes";
        if (attachmentInput.disabled) {
            attachmentInput.value = "";
        }
    };

    attachmentSelect.addEventListener("change", syncAttachmentInput);
    syncAttachmentInput();
});
